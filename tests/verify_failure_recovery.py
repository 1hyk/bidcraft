"""故障注入测试：不调用外部模型，不读写业务数据库。"""
import sys
import tempfile
import unittest
import threading
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services import writer_service as writer, compile_service, fact_service, requirement_matrix
from app.services.task_runner import ThreadPoolTaskRunner
real_finish_images=writer._finish_images


class InlineThread:
    def __init__(self,target,**kw):self.target=target
    def start(self):self.target()


class FailureRecovery(unittest.TestCase):
    def setUp(self):
        self.pid=987654
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.addCleanup(lambda:writer._batch_running.discard(self.pid))
        self.addCleanup(lambda:writer._stop_flags.pop(self.pid,None))
        self.nodes=[{'id':1,'title':'第一章'},{'id':2,'title':'第二章'}]
        self.saved={1:'原有正文一',2:'原有正文二'}
        self.progress=self.stack.enter_context(patch.object(compile_service,'update_progress'))
        self.execute=self.stack.enter_context(patch.object(writer,'execute'))
        self.stack.enter_context(patch.object(writer,'threading',SimpleNamespace(Thread=InlineThread,Event=threading.Event)))
        self.stack.enter_context(patch.object(writer,'_pending_nodes',return_value=self.nodes))
        self.stack.enter_context(patch.object(writer,'query',return_value=self.nodes))
        self.stack.enter_context(patch('app.services.section_service.generation_node_ids',return_value={1,2}))
        self.targets=self.stack.enter_context(patch.object(writer,'_target_map',return_value={}))
        self.stack.enter_context(patch('app.services.image_ownership.project_image_skips',return_value={}))
        self.stack.enter_context(patch.object(writer,'_image_plan',return_value={}))
        ctx={k:[] for k in ['facts','knowledge','siblings','experiences','methods','file_refs']}
        ctx.update(outline={},lot_info='',reqs='',node={'title':'测试章节'})
        self.stack.enter_context(patch.object(writer,'_chapter_context',return_value=ctx))
        self.stack.enter_context(patch.object(writer,'_finish_images',side_effect=lambda c,b,*a:b))
        self.stack.enter_context(patch.object(writer,'_used_summary',return_value={}))
        self.persist=self.stack.enter_context(patch.object(writer,'persist_one',side_effect=lambda p,n,b,**kw:self.saved.__setitem__(n,b)))
        self.model=self.stack.enter_context(patch.object(writer,'write_chapter',return_value='新正文'))

    def test_batch_model_timeout_is_failure_preserves_old_and_retry_works(self):
        self.model.side_effect=TimeoutError('model timeout')
        writer.start_batch(self.pid)
        self.assertEqual(self.progress.call_args.kwargs['stage'],'failed')
        self.assertEqual(self.saved,{1:'原有正文一',2:'原有正文二'})
        self.assertNotIn(self.pid,writer._batch_running)
        self.model.side_effect=None
        self.assertTrue(writer.start_batch(self.pid)['started'])
        self.assertEqual(self.progress.call_args.kwargs['stage'],'done')

    def test_preparation_failure_releases_both_batch_paths(self):
        for start in [lambda:writer.start_batch(self.pid),lambda:writer.rewrite_chapters(self.pid,[1,2])]:
            before=dict(self.saved)
            self.targets.side_effect=RuntimeError('plan unavailable')
            with self.assertRaises(RuntimeError):start()
            self.assertNotIn(self.pid,writer._batch_running)
            self.assertNotIn(self.pid,writer._stop_flags)
            self.assertEqual(self.saved,before)
            self.targets.side_effect=None
            self.assertTrue(start()['started'])

    def test_partial_failure_keeps_success_and_failed_old_body(self):
        def model(**kw):
            if not hasattr(model,'called'):model.called=True;return '成功的新正文'
            raise TimeoutError('timeout')
        self.model.side_effect=model
        writer.start_batch(self.pid)
        self.assertEqual(self.persist.call_count,1)
        self.assertIn('成功的新正文',self.saved.values())
        self.assertEqual(self.progress.call_args.kwargs['stage'],'failed')

    def test_stop_before_work_preserves_old_and_releases_state(self):
        with patch.object(writer,'_image_plan',side_effect=lambda p:(writer._stop_flags[p].set() or {})):
            writer.start_batch(self.pid)
        self.model.assert_not_called();self.persist.assert_not_called()
        self.assertEqual(self.progress.call_args.kwargs['stage'],'outlines_ready')
        self.assertNotIn(self.pid,writer._batch_running)

    def test_parse_missing_file_marks_failed_without_touching_body(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(fact_service,'settings',SimpleNamespace(uploads_dir=Path(folder))),patch.object(fact_service,'query_one',return_value={'file_path':'missing.docx'}),patch.object(fact_service,'execute') as execute,patch.object(fact_service,'_set_progress'):
            with self.assertRaises(FileNotFoundError):fact_service.process_file(1,self.pid,2)
            self.assertTrue(any("status = 'failed'" in c.args[0] for c in execute.call_args_list))
        self.persist.assert_not_called()

    def test_review_exception_releases_lock_for_retry(self):
        with patch('app.services.proposal_quality.review',side_effect=[TimeoutError('timeout'),{'ok':True}]):
            with self.assertRaises(TimeoutError):requirement_matrix.run_review(self.pid)
            self.assertEqual(requirement_matrix.run_review(self.pid),{'ok':True})

    def test_task_exception_is_not_recorded_as_success(self):
        with patch.object(ThreadPoolTaskRunner,'_update') as update:
            ThreadPoolTaskRunner._run(object.__new__(ThreadPoolTaskRunner),1,Mock(side_effect=TimeoutError('timeout')))
            self.assertEqual(update.call_args.kwargs['status'],'failed')

    def test_explicit_failed_task_result_is_not_success(self):
        with patch.object(ThreadPoolTaskRunner,'_update') as update:
            ThreadPoolTaskRunner._run(object.__new__(ThreadPoolTaskRunner),1,Mock(return_value={'ok':False,'error':'思路保存失败'}))
            self.assertEqual(update.call_args.kwargs['status'],'failed')
        with patch.object(ThreadPoolTaskRunner,'_update') as update:
            ThreadPoolTaskRunner._run(object.__new__(ThreadPoolTaskRunner),1,Mock(return_value={'ok':True}))
            self.assertEqual(update.call_args.kwargs['status'],'success')

    def test_task_submission_failure_is_retryable_not_running(self):
        runner=object.__new__(ThreadPoolTaskRunner);runner.pool=Mock()
        runner.pool.submit.side_effect=RuntimeError('executor unavailable')
        with patch('app.services.task_runner.execute',return_value=5),patch.object(runner,'_update') as update:
            with self.assertRaises(RuntimeError):runner.submit('test',1,Mock())
            self.assertEqual(update.call_args.kwargs['status'],'failed')

    def test_empty_model_output_is_not_saved(self):
        for value in ['', '   \n']:
            with self.assertRaises(ValueError):real_finish_images({},value,self.pid,1)
        self.persist.assert_not_called()

    def test_thread_start_failure_releases_state_and_can_retry(self):
        with patch.object(InlineThread,'start',side_effect=RuntimeError('thread unavailable')):
            with self.assertRaises(RuntimeError):writer.start_batch(self.pid)
        self.assertNotIn(self.pid,writer._batch_running)
        self.assertNotIn(self.pid,writer._stop_flags)
        self.assertTrue(writer.start_batch(self.pid)['started'])

    def test_stream_timeout_emits_error_and_preserves_old_body(self):
        def stream(**kw):
            yield '生成了一部分'
            raise TimeoutError('stream timeout')
        with patch.object(writer,'query_one',return_value={'title':'测试章节'}),patch.object(writer,'_target_for',return_value=None),patch.object(writer,'write_chapter_stream',side_effect=stream):
            events=list(writer.generate_chapter_events(self.pid,1))
        self.assertIn('chapter_error',[e['type'] for e in events])
        self.assertNotIn('chapter_done',[e['type'] for e in events])
        self.persist.assert_not_called()

    def test_stream_disconnect_keeps_partial_without_completion_event(self):
        with patch.object(writer,'query_one',return_value={'title':'测试章节'}),patch.object(writer,'_target_for',return_value=None),patch.object(writer,'write_chapter_stream',return_value=iter(['部分正文','其余正文'])):
            generator=writer.generate_chapter_events(self.pid,1)
            while next(generator)['type']!='chapter_delta':pass
            generator.close()
        self.assertEqual(self.saved[1],'部分正文')
        self.assertEqual(self.persist.call_count,1)
        self.assertIn("status='thought_ready'",self.execute.call_args.args[0])

    def test_restart_resets_stale_writing_without_deleting_body(self):
        from app import main
        cursor=Mock(rowcount=0);cursor.fetchone.return_value=None
        connection=Mock();connection.cursor.return_value=cursor
        with patch('app.core.database.get_conn',return_value=connection),patch.object(main.settings,'compile_auto_resume',False):
            main._startup()
        sqls=[c.args[0] for c in cursor.execute.call_args_list]
        self.assertTrue(any("WHERE stage='writing'" in sql and "stage='failed'" in sql for sql in sqls))
        self.assertFalse(any('section_content' in sql for sql in sqls))


if __name__=='__main__':unittest.main()

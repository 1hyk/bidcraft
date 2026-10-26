import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      // 公司资料当前不对外使用；保留旧地址跳回首页，避免收藏链接落到空白页。
      path: '/company',
      redirect: '/',
    },
    {
      path: '/',
      name: 'home',
      component: () => import('../views/HomeView.vue'),
    },
    {
      path: '/workspace/:projectId',
      name: 'workspace',
      component: () => import('../views/WorkspaceView.vue'),
      props: true,
      meta: { fullscreen: true },
    },
    {
      path: '/knowledge',
      name: 'knowledge',
      component: () => import('../views/KnowledgeView.vue'),
    },
    {
      path: '/knowledge/pages',
      name: 'knowledge-pages',
      component: () => import('../views/KnowledgePagesView.vue'),
    },
    {
      path: '/knowledge/methods',
      name: 'knowledge-methods',
      component: () => import('../views/MethodEntriesView.vue'),
    },
    {
      path: '/experience',
      name: 'experience',
      component: () => import('../views/ExperienceView.vue'),
    },
  ],
})

export default router

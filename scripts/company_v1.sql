CREATE TABLE IF NOT EXISTS company_profile (
 id BIGINT PRIMARY KEY COMMENT '本机默认投标主体标识',
 content JSON NOT NULL COMMENT '公司名称与业务定位',
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'
) COMMENT='公司基本档案';
CREATE TABLE IF NOT EXISTS company_document (
 id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '公司原件标识',
 name VARCHAR(255) NOT NULL COMMENT '原始文件名',
 sha256 CHAR(64) NOT NULL UNIQUE COMMENT '原件内容哈希用于去重',
 path VARCHAR(500) NOT NULL COMMENT '非公开静态目录中的原件路径',
 status VARCHAR(30) NOT NULL DEFAULT 'uploaded' COMMENT '上传或提取状态',
 error TEXT COMMENT '失败原因与读取限制',
 task_id BIGINT NULL COMMENT '提取任务标识',
 fragments JSON NULL COMMENT '带位置的提取原文片段',
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
) COMMENT='公司原始资料及提取状态';
CREATE TABLE IF NOT EXISTS company_fact (
 id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '事实候选标识',
 doc_id BIGINT NULL COMMENT '原件标识手工条目可为空',
 category VARCHAR(80) NOT NULL COMMENT '事实类别',
 subject VARCHAR(200) NOT NULL DEFAULT '' COMMENT '事实归属主体',
 field_name VARCHAR(200) NOT NULL DEFAULT '' COMMENT '事实字段名称',
 value TEXT NOT NULL COMMENT '事实原文值',
 evidence TEXT NOT NULL COMMENT '支持事实的连续原文证据',
 location VARCHAR(200) NOT NULL DEFAULT '' COMMENT '页码段落或表格位置',
 valid_until DATE NULL COMMENT '有效期截止日期空表示尚未确定',
 applicability TEXT COMMENT '适用范围与限制',
 status VARCHAR(30) NOT NULL DEFAULT 'pending' COMMENT 'pending候选confirmed已核实rejected否决disabled停用',
 revision INT NOT NULL DEFAULT 1 COMMENT '修订版本用于并发检查',
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
 INDEX idx_company_fact_doc(doc_id), INDEX idx_company_fact_status(status)
) COMMENT='有证据及审核状态的公司事实';
CREATE TABLE IF NOT EXISTS company_fill_revision (
 id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '补全修订标识',
 project_id BIGINT NOT NULL COMMENT '项目标识',
 node_id BIGINT NOT NULL COMMENT '正文目录节点标识',
 before_content JSON NOT NULL COMMENT '补全前编辑器正文载荷',
 after_content JSON NOT NULL COMMENT '补全后编辑器正文载荷',
 before_ai INT NOT NULL COMMENT '修改前AI标识',
 after_hash CHAR(64) NOT NULL COMMENT '修改后正文摘要用于安全撤回',
 facts JSON NOT NULL COMMENT '应用时事实及来源快照',
 status VARCHAR(30) NOT NULL DEFAULT 'applied' COMMENT 'applied已应用undone已撤回',
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '应用时间',
 INDEX idx_company_revision_project(project_id,node_id)
) COMMENT='公司事实补全修订与安全撤回记录';

# 基线说明（Baseline）

- **基线日期**：2026-10-27
- **状态**：基本完整 —— 生成、编辑、Mermaid 渲染、Word 导出、Linux 分页与部署链路均已跑通
- **Git 标签**：`baseline-2026-10-27`
- **用途**：原始项目基线。本提交不包含任何公司适配性改动。

## 后续改动约定

公司要求的适配改动请从本基线拉出 `company-adaptation` 分支进行，**不要直接改 `main`**。
这样 `main` 和 `baseline-2026-10-27` 始终保留原始项目状态，可随时对照或回退。

```bash
git checkout company-adaptation
# 在分支上修改、提交
```

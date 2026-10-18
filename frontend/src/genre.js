// 体裁词：本领域**要编制的这份文件**叫什么（施工组织设计 / 技术方案…）。
//
// 由后端声明（`Domain.genre`，见 backend/app/core/domains.py），随 `/api/domains` 下发。
// 前端**不硬编码任何领域的词**——这里原先散落着写死的「施工组织设计 / 施组 / 施工方案」，
// 那些是铁路的体裁；一个低空项目照抄过去，文案和它真正要交的东西对不上。
// 与后端 `fill_genre` 同一套口径：换一个领域，文案跟着域走，前端不改代码。
//
// 用法：`const g = computed(() => genreOf(domains.value, key))` → 模板里 `{{ g.full }}`。

//: 域不存在、或域没声明体裁词时的兜底。挑一个**任何领域都成立**的泛称，
//: 而不是某个领域的词——塞错领域的词比说得笼统更糟。
const FALLBACK = { full: '正文', short: '正文', bid: '正文' }

/**
 * 取某个领域的体裁词。
 * @param domains `/api/domains` 回来的清单（每项形如 `{key, name, genre:{full,short,bid}}`）
 * @param key     领域 key；空值时调用方应自己决定用哪个域（如默认域）
 */
export function genreOf(domains, key) {
  return (domains || []).find(d => d.key === key)?.genre || FALLBACK
}

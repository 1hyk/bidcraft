import { marked } from 'marked'

// 审阅文本只保留文档排版标签，不执行原文HTML、不加载外链图片。
export function reviewMarkup(text) {
  const root = document.createElement('div')
  root.innerHTML = marked.parse(String(text || ''))
  const allowed = new Set(['P','BR','H1','H2','H3','H4','H5','H6','STRONG','B','EM','I','UL','OL','LI','TABLE','THEAD','TBODY','TR','TH','TD','BLOCKQUOTE','PRE','CODE','HR','DEL'])
  for (const el of [...root.querySelectorAll('*')]) {
    if (['SCRIPT','STYLE','IFRAME','OBJECT','EMBED','SVG','MATH','FORM','INPUT','BUTTON'].includes(el.tagName)) {
      el.remove(); continue
    }
    if (el.tagName === 'IMG') {
      el.replaceWith(document.createTextNode(el.getAttribute('alt') ? `[图片：${el.getAttribute('alt')}]` : '[图片，详见原文件]'))
      continue
    }
    if (!allowed.has(el.tagName)) {
      el.replaceWith(...el.childNodes); continue
    }
    for (const attr of [...el.attributes]) {
      const keep = ['TH','TD'].includes(el.tagName) && ['colspan','rowspan'].includes(attr.name) && /^\d{1,2}$/.test(attr.value)
      if (!keep) el.removeAttribute(attr.name)
    }
  }
  return root.innerHTML
}

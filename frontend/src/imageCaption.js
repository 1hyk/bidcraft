export function displayImageCaption(value) {
  return String(value || '').replace(/([（(])AI生成示意[，,、\s]*待人工核对([）)])/g,'$1AI生成示意$2').replace(/[（(]待人工核对[）)]/g,'').trim()
}
export function figureTitle(value) {
  return displayImageCaption(value).replace(/[（(](?:AI生成示意|数据仅作示例，非实测结果)[）)]/g,'').trim()
}

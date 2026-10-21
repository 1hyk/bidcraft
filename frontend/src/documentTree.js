// 仅整理显示，不改变持久化树、节点ID或正文顺序。
export function splitDocumentTree(tree, indexRequired) {
  if(!indexRequired)return {frontMatter:[],body:tree || []}
  const frontMatter=[]
  function walk(nodes){
    return (nodes || []).flatMap(node=>{
      if(indexRequired && node.outline?._document_role==='scoring_index'){
        frontMatter.push(node)
        return []
      }
      return [node.children ? {...node,children:walk(node.children)} : {...node}]
    })
  }
  return {frontMatter,body:walk(tree)}
}

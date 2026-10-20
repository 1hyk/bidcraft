<template>
  <aside class="global-nav">
    <!-- 品牌 -->
    <div class="nav-brand">
      <div class="brand-text">
        <img class="brand-logo" src="/brand/jiachi-logo.png" alt="嘉驰国际 X-GIANTS" />
        <span class="brand-sub">嘉驰科技咨询 · 技术方案工作台</span>
      </div>
    </div>

    <!-- 导航项 -->
    <nav class="nav-list">
      <router-link
        v-for="item in NAV"
        :key="item.path"
        :to="item.path"
        class="nav-item"
        :class="{ active: isActive(item.path) }"
      >
        <span class="nav-icon" v-html="item.icon" />
        <span class="nav-label">{{ item.label }}</span>
      </router-link>
    </nav>

    <!-- 底部占位 -->
    <div class="nav-footer">
      <span class="nav-ver">嘉驰 · 内部工作空间</span>
    </div>
  </aside>
</template>

<script setup>
import { useRoute } from 'vue-router'

const route = useRoute()

const NAV = [
  {
    path: '/',
    label: '项目工作台',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/></svg>',
  },
  {path:'/company',label:'公司资料',icon:'▤'},
  {
    path: '/knowledge',
    label: '历史参考素材',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/><path d="M8 7h8M8 11h8M8 15h5"/></svg>',
  },
]

function isActive(path) {
  if (path === '/') return route.path === '/'
  return route.path.startsWith(path)
}
</script>

<style scoped>
.global-nav {
  width: 200px; flex-shrink: 0; height: 100%;
  background: var(--ink-900); border-right: 1px solid var(--ink-line);
  display: flex; flex-direction: column;
}

/* 品牌 */
.nav-brand { display: flex; align-items: center; gap: 10px; padding: 20px 16px 16px; border-bottom: 1px solid var(--ink-line); }
.brand-mark {
  width: 34px; height: 34px; flex-shrink: 0; border-radius: 8px;
  background: linear-gradient(135deg, var(--brand) 0%, var(--brand-deep) 100%);
  display: flex; align-items: center; justify-content: center; color: #fff;
  box-shadow: 0 2px 8px rgba(47, 95, 208, 0.4);
}
.brand-text { display: flex; flex-direction: column; min-width: 0; }
.brand-name { font-size: 13px; font-weight: 700; color: var(--ink-text); letter-spacing: 0.02em; white-space: nowrap; }
.brand-sub { font-size: 10px; color: var(--ink-text-3); margin-top: 1px; white-space: nowrap; }

/* 导航列表 */
.nav-list { flex: 1; padding: 12px 10px; display: flex; flex-direction: column; gap: 2px; }
.nav-item {
  display: flex; align-items: center; gap: 10px; padding: 9px 12px;
  border-radius: var(--radius-s); text-decoration: none; color: var(--ink-text-2);
  font-size: 13px; transition: all 0.15s ease-out;
}
.nav-item:hover { background: var(--ink-800); color: var(--ink-text); }
.nav-item.active { background: var(--brand); color: #fff; font-weight: 600; }
.nav-icon { display: flex; align-items: center; }
.nav-label { letter-spacing: 0.02em; }

.nav-footer { padding: 14px 16px; border-top: 1px solid var(--ink-line); }
.nav-ver { font-size: 10px; color: var(--ink-text-3); }
.global-nav { width:216px; background:#fff; border-right:1px solid #e2e8f0; }
.nav-brand { padding:30px 24px 26px; min-height:102px; border-color:#edf0f5; }
.brand-name { font-size:20px; color:#18385e; letter-spacing:.04em; }
.brand-logo { display:block; width:168px; height:32px; object-fit:contain; object-position:left center; }
.brand-sub { font-size:11px; color:#8794a7; margin-top:8px; letter-spacing:.04em; }
.nav-list { padding:24px 14px; gap:8px; }
.nav-item { padding:13px 16px; color:#69788d; border-radius:9px; }
.nav-item:hover { background:#f3f6fa; color:#284b7d; }
.nav-item.active { background:#eaf1fc; color:#2f5fd0; }
.nav-footer { border-color:#edf0f5; padding:20px 24px; }
.nav-ver { color:#93a0b1; }
@media(max-width:800px) { .global-nav { width:164px; } .nav-brand { padding:26px 16px; } .brand-logo { width:132px; height:28px; } .brand-sub { font-size:9px; } .nav-item { padding:12px 10px; } }
</style>

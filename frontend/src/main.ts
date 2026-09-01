import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import Antd from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import App from './App.vue'
import Home from './views/Home.vue'
import Result from './views/Result.vue'
import History from './views/History.vue'
import Login from './views/Login.vue'
import Knowledge from './views/Knowledge.vue'
import KnowledgeAdmin from './views/KnowledgeAdmin.vue'
import Research from './views/Research.vue'
import { isAuthenticated } from './services/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'Home',
      component: Home
    },
    {
      path: '/result',
      name: 'Result',
      component: Result
    },
    {
      path: '/history',
      name: 'History',
      component: History,
      meta: { requiresAuth: true } // 需登录
    },
    {
      path: '/login',
      name: 'Login',
      component: Login
    },
    {
      path: '/knowledge',
      name: 'Knowledge',
      component: Knowledge,
      meta: { requiresAuth: true }
    },
    {
      path: '/knowledge/admin',
      name: 'KnowledgeAdmin',
      component: KnowledgeAdmin,
      meta: { requiresAuth: true }
    },
    {
      path: '/research',
      name: 'Research',
      component: Research,
      meta: { requiresAuth: true }
    }
  ]
})

// 路由守卫: 需登录的页面未登录 → 跳登录页 (带回跳地址)
router.beforeEach((to) => {
  if (to.meta.requiresAuth && !isAuthenticated()) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  return true
})

const app = createApp(App)

app.use(router)
app.use(Antd)

app.mount('#app')

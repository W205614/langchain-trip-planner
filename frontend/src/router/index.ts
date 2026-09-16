import { createRouter, createWebHistory } from 'vue-router'
const Home = () => import('../views/Home.vue')
const Result = () => import('../views/Result.vue')
const History = () => import('../views/History.vue')
const Login = () => import('../views/Login.vue')
const Knowledge = () => import('../views/Knowledge.vue')
const KnowledgeAdmin = () => import('../views/KnowledgeAdmin.vue')
const Research = () => import('../views/Research.vue')
import { isAuthenticated } from '../services/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/tasks', component: () => import('../views/Tasks.vue'), meta: { requiresAuth: true } },
    { path: '/explore', component: () => import('../views/Explore.vue') },
    { path: '/favorites', component: () => import('../views/Favorites.vue'), meta: { requiresAuth: true } },
    { path: '/trips/new', component: () => import('../views/ManualTrip.vue'), meta: { requiresAuth: true } },
    { path: '/assistant', component: () => import('../views/Assistant.vue'), meta: { requiresAuth: true } },
    { path: '/shared/:token', component: () => import('../views/SharedTrip.vue') },
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

export default router

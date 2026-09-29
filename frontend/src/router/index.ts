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
    { path: '/tasks', redirect: { path: '/history', query: { tab: 'tasks' } }, meta: { requiresAuth: true } },
    { path: '/explore', component: () => import('../views/Explore.vue') },
    { path: '/community', component: () => import('../views/Community.vue') },
    { path: '/community/admin', component: () => import('../views/CommunityAdmin.vue'), meta: { requiresAuth: true } },
    { path: '/favorites', redirect: '/explore' },
    { path: '/trips/new', redirect: '/' },
    { path: '/trips/:id/operations', component: () => import('../views/TripWorkspace.vue'), meta: { requiresAuth: true } },
    { path: '/inbox', component: () => import('../views/TripInbox.vue'), meta: { requiresAuth: true } },
    { path: '/assistant', redirect: '/history' },
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

// A rebuilt frontend replaces hashed lazy chunks. An already-open tab may still
// request an old chunk when the user visits another page; reload that route once
// so it receives the new asset manifest while preserving its query string.
const chunkReloadKey = 'chunkReloadAttempt'
router.onError((error, to) => {
  if (!/failed to fetch dynamically imported module|importing a module script failed|error loading dynamically imported module/i.test(String(error))) return
  if (sessionStorage.getItem(chunkReloadKey) === to.fullPath) return
  sessionStorage.setItem(chunkReloadKey, to.fullPath)
  window.location.assign(to.fullPath)
})
router.afterEach((_to, _from, failure) => {
  if (!failure) sessionStorage.removeItem(chunkReloadKey)
})

export default router

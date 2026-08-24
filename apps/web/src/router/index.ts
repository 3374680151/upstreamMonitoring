import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: () => import('@/pages/OverviewPage.vue') },
    { path: '/sites', name: 'sites', component: () => import('@/pages/SitesPage.vue') },
    { path: '/detail', name: 'detail', component: () => import('@/pages/DetailPage.vue') },
    { path: '/detail/:id', name: 'detail-id', component: () => import('@/pages/DetailPage.vue') },
    { path: '/changes', name: 'changes', component: () => import('@/pages/ChangesPage.vue') },
    { path: '/balance', name: 'balance', component: () => import('@/pages/BalancePage.vue') },
    { path: '/channels', name: 'channels', component: () => import('@/pages/ChannelsPage.vue') },
    { path: '/notifications', name: 'notifications', component: () => import('@/pages/NotificationsPage.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

export default router

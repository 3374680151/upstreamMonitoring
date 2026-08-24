import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './styles/tokens.css'

const saved = localStorage.getItem('upstream-theme')
if (saved === 'dark' || saved === 'light') {
  document.documentElement.setAttribute('data-theme', saved)
}

createApp(App).use(router).mount('#app')

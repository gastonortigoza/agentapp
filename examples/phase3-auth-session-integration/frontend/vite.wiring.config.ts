import {defineConfig} from 'vite';
export default defineConfig({esbuild:{jsx:'automatic'},build:{outDir:'/tmp/auth-wiring-dist',rollupOptions:{input:'wiring.html'}}});

import { defineConfig } from 'vite';
// A published synthetic PNG fixture, not the unimplemented upload pipeline.
const image=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');
export default defineConfig({cacheDir:'/tmp/vite-cache',esbuild:{jsx:'automatic'},plugins:[{name:'synthetic-photo-fixture',
  configureServer(server){server.middlewares.use('/synthetic-placeholder.png',(_req,res)=>{res.setHeader('Content-Type','image/png');res.end(image);});},
  generateBundle(){this.emitFile({type:'asset',fileName:'synthetic-placeholder.png',source:image});}}],
  server:{host:'127.0.0.1',port:5173,strictPort:true,proxy:{'/api':'http://127.0.0.1:3000'}}});

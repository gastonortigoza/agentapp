import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./e2e',timeout:30000,retries:0,workers:1,reporter:'list',outputDir:'/tmp/playwright-results',use:{baseURL:'http://127.0.0.1:5173',headless:true,launchOptions:{chromiumSandbox:false}},projects:[{name:'chromium',use:{browserName:'chromium'}}]});

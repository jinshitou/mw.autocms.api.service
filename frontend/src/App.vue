<template>
  <div id="app">
    <div class="top-header">
      <h1>🚀 易优站群自动化引擎 <span>v1.0</span></h1>
    </div>
    
    <div class="main-content">
      <el-tabs v-model="activeTab" class="nav-tabs" type="border-card">
        <el-tab-pane label="⚡ 批量一键建站" name="deploy">
          <DeployManager />
        </el-tab-pane>
        <el-tab-pane label="🖥️ 服务器资产管理" name="servers">
          <ServerManager />
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import ServerManager from './components/ServerManager.vue'
import DeployManager from './components/DeployManager.vue'

// 默认打开批量建站页面
const activeTab = ref('deploy')
</script>

<style>
body {
  margin: 0;
  background-color: #f0f2f5;
  font-family: 'Helvetica Neue', Helvetica, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', Arial, sans-serif;
}
.top-header {
  background-color: #2b303b;
  color: white;
  padding: 15px 30px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
}
.top-header h1 {
  margin: 0;
  font-size: 22px;
  letter-spacing: 1px;
}
.top-header span {
  font-size: 12px;
  color: #409EFF;
  border: 1px solid #409EFF;
  padding: 2px 6px;
  border-radius: 12px;
  vertical-align: middle;
  margin-left: 10px;
}
.main-content {
  padding: 30px;
  max-width: 1200px;
  margin: 0 auto;
}
.nav-tabs {
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.05) !important;
}
</style>

<template>
  <div class="deploy-container">
    <el-row :gutter="20">
      <el-col :span="10">
        <el-card shadow="hover" class="config-card">
          <template #header>
            <div class="card-header">
              <span>⚙️ 基础配置</span>
            </div>
          </template>
          <el-form :model="form" label-width="100px" label-position="left">
            <el-form-item label="目标服务器" required>
              <el-select v-model="form.server_id" placeholder="请选择要部署的服务器" style="width: 100%" v-loading="loadingServers">
                <el-option
                  v-for="server in servers"
                  :key="server.id"
                  :label="`${server.name} (${server.ip_address})`"
                  :value="server.id"
                  :disabled="!server.is_active"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="模板包名称" required>
              <el-input v-model="form.template_key" placeholder="如：eyoucms_core.zip" />
            </el-form-item>
            <el-form-item label="后台路径" required>
              <el-input v-model="form.admin_path" placeholder="如：console_admin.php" />
            </el-form-item>
            
            <el-divider>SEO TDK 设置</el-divider>
            
            <el-form-item label="默认标题">
              <el-input v-model="form.tdk_config.title" placeholder="全局默认 Title" />
            </el-form-item>
            <el-form-item label="默认关键词">
              <el-input v-model="form.tdk_config.keywords" placeholder="全局默认 Keywords" />
            </el-form-item>
            <el-form-item label="默认描述">
              <el-input type="textarea" v-model="form.tdk_config.description" placeholder="全局默认 Description" />
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>

      <el-col :span="14">
        <el-card shadow="hover" class="site-card">
          <template #header>
            <div class="card-header">
              <span>🌐 批量站点录入</span>
              <el-tag type="info">支持批量粘贴</el-tag>
            </div>
          </template>
          
          <div class="tip-box">
            <p>请按格式每行输入一个站点：<code>域名,绑定IP</code> (使用英文逗号)</p>
            <p>例如：<br/>www.test1.com,192.168.1.100<br/>www.test2.com,192.168.1.101</p>
          </div>

          <el-input
            v-model="rawSitesInput"
            type="textarea"
            :rows="12"
            placeholder="在此处粘贴你的站群列表..."
            class="site-textarea"
          />
          
          <div class="action-bar">
            <div class="stat">
              已识别有效站点：<span class="highlight">{{ parsedSites.length }}</span> 个
            </div>
            <el-button 
              type="primary" 
              size="large" 
              icon="Position" 
              @click="submitDeploy" 
              :loading="deployLoading"
              :disabled="parsedSites.length === 0">
              🚀 立即下发执行
            </el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import request from '../utils/request'

// 数据状态
const servers = ref([])
const loadingServers = ref(false)
const deployLoading = ref(false)
const rawSitesInput = ref('')

// 表单数据绑定
const form = ref({
  server_id: null,
  template_key: 'eyoucms_core.zip',
  admin_path: 'console_v8x.php',
  tdk_config: {
    title: '全新科技企业官网',
    keywords: '科技,企业,官网',
    description: '提供最优质的科技资讯与服务。'
  }
})

// 获取可用服务器列表
const fetchServers = async () => {
  loadingServers.value = true
  try {
    servers.value = await request.get('/servers/')
  } finally {
    loadingServers.value = false
  }
}

// 动态计算属性：解析左侧文本框里的批量站点
const parsedSites = computed(() => {
  if (!rawSitesInput.value.trim()) return []
  const lines = rawSitesInput.value.split('\n')
  const validSites = []
  
  lines.forEach(line => {
    const parts = line.split(',').map(item => item.trim())
    // 必须有域名和 IP 两个部分
    if (parts.length >= 2 && parts[0] !== '' && parts[1] !== '') {
      validSites.push({
        domain: parts[0],
        bind_ip: parts[1]
      })
    }
  })
  return validSites
})

// 提交批量部署任务
const submitDeploy = async () => {
  if (!form.value.server_id) {
    return ElMessage.warning('请先在左侧选择目标服务器！')
  }
  
  const payload = {
    server_id: form.value.server_id,
    template_key: form.value.template_key,
    admin_path: form.value.admin_path,
    tdk_config: form.value.tdk_config,
    sites: parsedSites.value
  }

  await ElMessageBox.confirm(`确认向目标服务器下发这 ${parsedSites.value.length} 个建站任务吗？`, '最终确认', {
    confirmButtonText: '⚡ 确认下发',
    cancelButtonText: '取消',
    type: 'warning'
  })

  deployLoading.value = true
  try {
    const res = await request.post('/deploy/batch', payload)
    if (res.status === 'success') {
      ElNotification({
        title: '指令下发成功！',
        message: res.message,
        type: 'success',
        duration: 8000
      })
      rawSitesInput.value = '' // 清空输入框
    }
  } finally {
    deployLoading.value = false
  }
}

onMounted(() => {
  fetchServers()
})
</script>

<style scoped>
.deploy-container {
  padding: 20px;
  max-width: 1200px;
  margin: 0 auto;
}
.card-header {
  font-weight: bold;
  font-size: 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.tip-box {
  background-color: #f4f4f5;
  padding: 10px 15px;
  border-radius: 4px;
  border-left: 4px solid #909399;
  margin-bottom: 15px;
  font-size: 13px;
  color: #606266;
}
.tip-box p {
  margin: 5px 0;
}
.site-textarea {
  margin-bottom: 20px;
  font-family: monospace;
}
.action-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 10px;
}
.highlight {
  color: #409EFF;
  font-size: 20px;
  font-weight: bold;
  padding: 0 5px;
}
</style>

<template>
  <div class="server-container">
    <div class="header">
      <h2>🖥️ 目标服务器资产管理</h2>
      <el-button type="primary" @click="dialogVisible = true"> + 添加服务器</el-button>
    </div>

    <el-table :data="servers" border style="width: 100%" v-loading="loading">
      <el-table-column prop="id" label="ID" width="60" align="center" />
      <el-table-column prop="name" label="服务器备注" />
      <el-table-column prop="ip_address" label="外网 IP" />
      <el-table-column label="宝塔面板地址">
        <template #default="scope">
          {{ scope.row.bt_protocol }}://{{ scope.row.ip_address }}:{{ scope.row.bt_port }}
        </template>
      </el-table-column>
      <el-table-column prop="is_active" label="状态" width="80" align="center">
        <template #default="scope">
          <el-tag :type="scope.row.is_active ? 'success' : 'danger'">
            {{ scope.row.is_active ? '在线' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" align="center">
        <template #default="scope">
          <el-button 
            size="small" 
            type="success" 
            @click="handleTest(scope.row.id)" 
            :loading="testLoading === scope.row.id">
            测试
          </el-button>
          <el-button size="small" type="danger" @click="handleDelete(scope.row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" title="新增服务器资产" width="500px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="备注名称" required>
          <el-form-item><el-input v-model="form.name" placeholder="如：香港站群 01号机" /></el-form-item>
        </el-form-item>
        <el-form-item label="外网 IP" required>
          <el-form-item><el-input v-model="form.ip_address" placeholder="如：192.168.1.100" /></el-form-item>
        </el-form-item>
        <el-form-item label="面板协议">
          <el-radio-group v-model="form.bt_protocol">
            <el-radio label="http">HTTP</el-radio>
            <el-radio label="https">HTTPS</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="面板端口">
          <el-form-item><el-input-number v-model="form.bt_port" :min="1" :max="65535" /></el-form-item>
        </el-form-item>
        <el-form-item label="宝塔 API Key" required>
          <el-form-item><el-input v-model="form.bt_key" type="password" show-password placeholder="请输入宝塔面板的接口密钥" /></el-form-item>
        </el-form-item>
      </el-form>
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="dialogVisible = false">取消</el-button>
          <el-button type="primary" @click="submitForm" :loading="submitLoading">确认添加</el-button>
        </span>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '../utils/request'

const servers = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const submitLoading = ref(false)
const testLoading = ref(null) // 用于控制测试按钮的 Loading 状态

const form = ref({
  name: '',
  ip_address: '',
  bt_protocol: 'http',
  bt_port: 8888,
  bt_key: ''
})

const fetchServers = async () => {
  loading.value = true
  try {
    servers.value = await request.get('/servers/')
  } finally {
    loading.value = false
  }
}

const submitForm = async () => {
  if (!form.value.name || !form.value.ip_address || !form.value.bt_key) {
    return ElMessage.warning('请填写完整的必填项！')
  }
  submitLoading.value = true
  try {
    await request.post('/servers/', form.value)
    ElMessage.success('服务器添加成功！')
    dialogVisible.value = false
    form.value = { name: '', ip_address: '', bt_protocol: 'http', bt_port: 8888, bt_key: '' }
    fetchServers()
  } finally {
    submitLoading.value = false
  }
}

const handleDelete = async (id) => {
  await ElMessageBox.confirm('确认删除该服务器吗？删除后不可恢复！', '警告', { type: 'warning' })
  await request.delete(`/servers/${id}`)
  ElMessage.success('删除成功')
  fetchServers()
}

// 🚀 新增：测试连通性核心逻辑
const handleTest = async (id) => {
  testLoading.value = id
  try {
    const res = await request.post(`/servers/${id}/test`)
    if (res.status === 'success') {
      ElMessage.success(res.message)
    } else {
      // 故意没有抛出 HTTP 异常，而是通过 JSON 状态码返回，方便友好提示
      ElMessage.error({ message: res.message, duration: 5000 }) 
    }
  } catch (error) {
    // 这里的 catch 会被 request.js 的拦截器捕获
  } finally {
    testLoading.value = null
  }
}

onMounted(() => {
  fetchServers()
})
</script>

<style scoped>
.server-container {
  padding: 20px;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
  margin: 20px auto;
  max-width: 1000px;
}
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}
</style>

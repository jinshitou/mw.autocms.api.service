import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({
    baseURL: 'http://127.0.0.1:8230/api', // 指向我们刚刚写好的 FastAPI 后端
    timeout: 10000
})

// 响应拦截器：统一处理报错，不用每次请求都写 catch
request.interceptors.response.use(
    response => response.data,
    error => {
        const msg = error.response?.data?.detail || error.message || '网络请求失败'
        ElMessage.error(msg)
        return Promise.reject(error)
    }
)

export default request

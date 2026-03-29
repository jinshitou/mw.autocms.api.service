import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

from core.obs_client import OBSClient

def test_obs():
    print("=========================================")
    print("📡 正在测试华为云 OBS 连通性...")
    print("=========================================")
    
    try:
        client = OBSClient()
        test_key = "test_dir/hello_obs.txt"
        test_content = b"Hello! This is a test file from AutoCMS Engine."
        
        print(f"[*] 准备上传测试文件至: {test_key}")
        success = client.upload_file_bytes(test_key, test_content)
        
        if success:
            print("✅ 连通性测试成功！你的 AK、SK 和 桶名称 配置完全正确！")
            print("👉 建议：既然连通没问题，说明刚才网页报错是因为前端超时时长不够。我已经在最新的前端代码里把超时改成了 3 分钟。")
        else:
            print("❌ 上传失败！虽然没报错，但 OBS 客户端返回了 False。")
    except Exception as e:
        print(f"❌ 致命错误：连通失败！\n报错详情：{str(e)}")
        print("👉 请检查 backend/.env 文件里的 OBS_AK, OBS_SK, OBS_ENDPOINT, OBS_BUCKET 是否填写正确。")

if __name__ == "__main__":
    test_obs()
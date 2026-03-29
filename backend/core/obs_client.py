import boto3
from botocore.client import Config
from core.config import settings

class OBSClient:
    def __init__(self):
        # 修正 1: 确保配置里的 endpoint 带有 https://
        endpoint = settings.obs_endpoint
        if not endpoint.startswith('http'):
            endpoint = f"https://{endpoint}"

        # 华为 OBS 完美兼容 AWS S3 协议
        self.s3 = boto3.client(
            's3',
            # 修正 2: 必须指定 region_name，香港是 ap-southeast-1
            region_name='ap-southeast-1', 
            endpoint_url=endpoint,
            aws_access_key_id=settings.obs_ak,
            aws_secret_access_key=settings.obs_sk,
            config=Config(
                signature_version='s3v4',
                # 修正 3: 强制使用路径风格访问（可选，但更稳健）
                s3={'addressing_style': 'virtual'} 
            )
        )

    def get_presigned_url(self, object_key: str, expiration=300) -> str:
        """生成私有桶文件的临时下载链接"""
        try:
            url = self.s3.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': settings.obs_bucket, 
                    'Key': object_key
                },
                ExpiresIn=expiration
            )
            return url
        except Exception as e:
            # 打印错误方便排查
            print(f"生成签名链接失败: {e}")
            return ""

    def upload_file_bytes(self, object_key: str, file_bytes: bytes) -> bool:
        """上传字节流到OBS"""
        try:
            self.s3.put_object(
                Bucket=settings.obs_bucket,
                Key=object_key,
                Body=file_bytes
            )
            return True
        except Exception as e:
            print(f"上传文件到OBS失败: {e}")
            return False
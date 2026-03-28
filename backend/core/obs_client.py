import boto3
from botocore.client import Config
from core.config import settings

class OBSClient:
    def __init__(self):
        # 华为 OBS 完美兼容 AWS S3 协议
        self.s3 = boto3.client(
            's3',
            endpoint_url=settings.obs_endpoint,
            aws_access_key_id=settings.obs_ak,
            aws_secret_access_key=settings.obs_sk,
            config=Config(signature_version='s3v4')
        )

    def get_presigned_url(self, object_key: str, expiration=300) -> str:
        """生成私有桶文件的临时下载链接 (默认 5 分钟有效)"""
        return self.s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': settings.obs_bucket, 'Key': object_key},
            ExpiresIn=expiration
        )
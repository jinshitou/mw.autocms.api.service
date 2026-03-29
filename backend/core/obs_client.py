import boto3
from botocore.client import Config
from core.config import settings

class OBSClient:
    def __init__(self):
        # 兼容 .env 中可能出现的注释或多余空格，避免 endpoint 解析异常
        endpoint = (settings.obs_endpoint or "").split("#", 1)[0].strip().strip("'\"")
        if not endpoint:
            endpoint = f"obs.{settings.obs_region}.myhuaweicloud.com"
        if not endpoint.startswith('http'):
            endpoint = f"https://{endpoint}"

        region = (settings.obs_region or "").strip() or "ap-southeast-1"
        self.bucket = (settings.obs_bucket or "").strip()

        # 华为 OBS 兼容 AWS S3 协议
        self.s3 = boto3.client(
            's3',
            region_name=region,
            endpoint_url=endpoint,
            aws_access_key_id=settings.obs_ak,
            aws_secret_access_key=settings.obs_sk,
            config=Config(
                signature_version='s3v4',
                # OBS 在 path-style 下通常更稳定
                s3={'addressing_style': 'path'}
            )
        )

    def get_presigned_url(self, object_key: str, expiration=300) -> str:
        """生成私有桶文件的临时下载链接"""
        try:
            url = self.s3.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket,
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
        if not self.bucket:
            raise RuntimeError("OBS_BUCKET 未配置")
        if not settings.obs_ak or not settings.obs_sk:
            raise RuntimeError("OBS_AK / OBS_SK 未配置")

        self.s3.put_object(
            Bucket=self.bucket,
            Key=object_key,
            Body=file_bytes
        )
        return True

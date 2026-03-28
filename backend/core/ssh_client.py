import asyncssh
from core.config import settings

async def execute_remote_cmd(host: str, port: int, command: str) -> str:
    """通过 RSA 密钥连接目标站群服务器执行 Shell 命令"""
    try:
        # 使用私钥连接 (无密码)
        async with asyncssh.connect(
            host, 
            port=port, 
            username='root', 
            client_keys=[settings.ssh_private_key_path],
            known_hosts=None # 自动接受未知主机的指纹
        ) as conn:
            result = await conn.run(command, check=True)
            return result.stdout
    except asyncssh.Error as exc:
        raise Exception(f"SSH 执行失败 [{host}]: {str(exc)}")
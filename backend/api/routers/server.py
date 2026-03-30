from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import time
import hashlib
import httpx
import ipaddress
import socket

from core.database import get_db
from models.server import Server
from schemas.server import ServerCreate, ServerResponse, ServerSshPortUpdate

router = APIRouter()

# 💡 核心魔法：智能解析多种 IP 格式
def parse_ip_pool(raw_text: str) -> str:
    expanded_ips = set()
    lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
    
    for line in lines:
        try:
            if '/' in line:  # 处理 CIDR 格式 (如: 38.239.36.0/24)
                network = ipaddress.ip_network(line, strict=False)
                # .hosts() 会自动剔除网络号和广播地址，只留可用 IP
                for ip in network.hosts():
                    expanded_ips.add(str(ip))
            elif '-' in line:  # 处理连续段格式 (如: 38.239.188.34-62)
                base_ip, end_octet = line.split('-')
                start_ip = ipaddress.ip_address(base_ip)
                base_parts = base_ip.split('.')
                end_ip_str = f"{base_parts[0]}.{base_parts[1]}.{base_parts[2]}.{end_octet}"
                end_ip = ipaddress.ip_address(end_ip_str)
                
                start_int = int(start_ip)
                end_int = int(end_ip)
                if start_int > end_int:
                    raise ValueError("起始位不能大于结束位")
                    
                for ip_int in range(start_int, end_int + 1):
                    expanded_ips.add(str(ipaddress.ip_address(ip_int)))
            else:  # 处理单 IP 格式
                ip = ipaddress.ip_address(line)
                expanded_ips.add(str(ip))
        except Exception as e:
            raise ValueError(f"行解析失败 [{line}]")

    # 去重并排序后，用逗号拼接存入数据库
    return ",".join(sorted(list(expanded_ips), key=lambda ip: ipaddress.IPv4Address(ip)))

@router.post("/", response_model=ServerResponse)
def create_server(server: ServerCreate, db: Session = Depends(get_db)):
    db_server = db.query(Server).filter(Server.main_ip == server.main_ip).first()
    if db_server:
        raise HTTPException(status_code=400, detail="该主控 IP 已存在！")
    
    try:
        # 在存入数据库前，拦截并自动展开前端传来的混合 IP 格式
        parsed_ip_string = parse_ip_pool(server.ip_pool)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    new_server_data = server.model_dump()
    new_server_data['ip_pool'] = parsed_ip_string  # 替换成展开后的纯净 IP 库
    
    new_server = Server(**new_server_data)
    db.add(new_server)
    db.commit()
    db.refresh(new_server)
    return new_server

@router.get("/", response_model=List[ServerResponse])
def get_servers(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Server).offset(skip).limit(limit).all()

@router.delete("/{server_id}")
def delete_server(server_id: int, db: Session = Depends(get_db)):
    db_server = db.query(Server).filter(Server.id == server_id).first()
    if not db_server:
        raise HTTPException(status_code=404, detail="未找到")
    db.delete(db_server)
    db.commit()
    return {"status": "success"}


@router.put("/{server_id}/ssh-port", response_model=ServerResponse)
def update_server_ssh_port(server_id: int, payload: ServerSshPortUpdate, db: Session = Depends(get_db)):
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="未找到服务器")
    ssh_port = int(payload.ssh_port or 0)
    if ssh_port < 1 or ssh_port > 65535:
        raise HTTPException(status_code=400, detail="ssh_port 必须在 1-65535")
    server.ssh_port = ssh_port
    db.commit()
    db.refresh(server)
    return server

@router.post("/{server_id}/test")
async def test_server_connection(server_id: int, db: Session = Depends(get_db)):
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="未找到服务器")

    bt_url = f"{server.bt_protocol}://{server.main_ip}:{server.bt_port}"
    now_time = int(time.time())
    md5_key = hashlib.md5(server.bt_key.encode('utf-8')).hexdigest()
    request_token = hashlib.md5((str(now_time) + md5_key).encode('utf-8')).hexdigest()
    
    try:
        async with httpx.AsyncClient(verify=False, timeout=8.0) as client:
            res = await client.post(f"{bt_url}/system?action=GetSystemTotal", data={'request_time': now_time, 'request_token': request_token})
        if res.status_code != 200:
            return {"status": "error", "message": "宝塔 HTTP 状态码异常"}
        res_data = res.json()
        if isinstance(res_data, dict) and res_data.get('status') is False:
            return {"status": "error", "message": f"宝塔拒绝: {res_data.get('msg')}"}

        ssh_port = int(getattr(server, "ssh_port", 22) or 22)
        try:
            with socket.create_connection((server.main_ip, ssh_port), timeout=3):
                pass
            return {"status": "success", "message": f"宝塔连接成功，SSH({ssh_port}) 端口可达。"}
        except Exception as ssh_exc:
            return {"status": "error", "message": f"宝塔连接成功，但 SSH({ssh_port}) 不可达: {ssh_exc}"}
    except Exception as e:
        return {"status": "error", "message": f"探测失败: {str(e)}"}

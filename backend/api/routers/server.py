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
from models.site import Site
from services.audit_service import log_operation
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
    log_operation(
        db,
        action="server.create",
        message=f"新增服务器: {new_server.name} ({new_server.main_ip})",
        detail={"server_id": new_server.id, "ssh_port": new_server.ssh_port, "bt_port": new_server.bt_port},
    )
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
    log_operation(db, action="server.delete", message=f"删除服务器: {db_server.name} ({db_server.main_ip})", detail={"server_id": server_id})
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
    log_operation(db, action="server.update_ssh_port", message=f"更新SSH端口: {server.name} -> {ssh_port}", detail={"server_id": server.id, "ssh_port": ssh_port})
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


def _extract_percent(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("%", "").strip()
    try:
        num = float(text)
        if num < 0:
            return None
        return round(num, 2)
    except Exception:
        return None


def _to_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


async def _fetch_server_status(server: Server, db: Session):
    bt_ok = False
    ssh_ok = False
    cpu = mem = disk = None
    bt_msg = ""
    ssh_msg = ""

    bt_url = f"{server.bt_protocol}://{server.main_ip}:{server.bt_port}"
    now_time = int(time.time())
    md5_key = hashlib.md5(server.bt_key.encode('utf-8')).hexdigest()
    request_token = hashlib.md5((str(now_time) + md5_key).encode('utf-8')).hexdigest()
    try:
        async with httpx.AsyncClient(verify=False, timeout=8.0) as client:
            res = await client.post(
                f"{bt_url}/system?action=GetSystemTotal",
                data={'request_time': now_time, 'request_token': request_token}
            )
        if res.status_code == 200:
            data = res.json()
            if not (isinstance(data, dict) and data.get('status') is False):
                bt_ok = True
                bt_msg = "ok"
                cpu = _extract_percent(data.get("cpuRealUsed") or data.get("cpu") or data.get("cpuRate") or data.get("cpuUsed"))
                mem_total = _to_float(data.get("memTotal"))
                mem_used = _to_float(data.get("memRealUsed"))
                if mem_total and mem_total > 0 and mem_used is not None:
                    mem = round((mem_used / mem_total) * 100, 2)
                else:
                    mem = _extract_percent(data.get("memRate"))

                try:
                    async with httpx.AsyncClient(verify=False, timeout=8.0) as client:
                        dres = await client.post(
                            f"{bt_url}/system?action=GetDiskInfo",
                            data={'request_time': now_time, 'request_token': request_token}
                        )
                    if dres.status_code == 200:
                        disk_data = dres.json()
                        if isinstance(disk_data, list) and disk_data:
                            root = next((item for item in disk_data if str(item.get("path")) == "/"), disk_data[0])
                            size = root.get("size") if isinstance(root, dict) else None
                            if isinstance(size, list) and len(size) >= 4:
                                disk = _extract_percent(size[3])
                except Exception:
                    pass
            else:
                bt_msg = str(data.get("msg") or "业务失败")
        else:
            bt_msg = f"http {res.status_code}"
    except Exception as exc:
        bt_msg = str(exc)

    ssh_port = int(getattr(server, "ssh_port", 22) or 22)
    try:
        with socket.create_connection((server.main_ip, ssh_port), timeout=3):
            pass
        ssh_ok = True
        ssh_msg = "ok"
    except Exception as exc:
        ssh_msg = str(exc)

    ip_list = [item for item in (server.ip_pool or "").split(",") if item.strip()]
    site_count = db.query(Site).filter(Site.server_id == server.id).count()

    return {
        "server_id": server.id,
        "name": server.name,
        "main_ip": server.main_ip,
        "cpu_percent": cpu,
        "memory_percent": mem,
        "disk_percent": disk,
        "site_count": site_count,
        "available_ip_count": len(ip_list),
        "bt_ok": bt_ok,
        "ssh_ok": ssh_ok,
        "bt_message": bt_msg,
        "ssh_message": ssh_msg,
    }


@router.get("/status-summary")
async def get_server_status_summary(db: Session = Depends(get_db)):
    servers = db.query(Server).all()
    items = []
    for server in servers:
        items.append(await _fetch_server_status(server, db))
    return {"status": "success", "items": items}


@router.get("/{server_id}/status")
async def get_server_status(server_id: int, db: Session = Depends(get_db)):
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="未找到服务器")
    item = await _fetch_server_status(server, db)
    return {"status": "success", "item": item}

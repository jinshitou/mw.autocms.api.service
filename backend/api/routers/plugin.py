import json
from typing import Optional
import tempfile
import subprocess
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from core.database import get_db
from models.asset import PluginPackage, PluginVersion, SitePluginDeployment
from models.site import Site
from models.server import Server
from schemas.plugin import (
    PluginResponse,
    PluginVersionResponse,
    PluginSitePageResponse,
    PluginSiteItem,
    PluginUpsertRequest,
    PluginUpdateRequest,
    PluginRedeployRequest,
)
from services.audit_service import create_task_log, update_task_log, log_operation
from services.plugin_deploy_service import (
    DEFAULT_PLUGIN_TYPE,
    DEFAULT_PLUGIN_VERSION,
    bump_patch,
    ensure_default_redirect_plugin,
    is_version_gt,
)
from worker.deploy_tasks import process_plugin_redeploy_batch

router = APIRouter()


def _parse_json(text: str):
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {}


def _php_lint_or_raise(code: str):
    with tempfile.NamedTemporaryFile(suffix=".php", delete=True) as fp:
        fp.write((code or "").encode("utf-8"))
        fp.flush()
        try:
            proc = subprocess.run(["php", "-l", fp.name], capture_output=True, text=True, timeout=20)
        except FileNotFoundError:
            raise HTTPException(status_code=400, detail="服务器未安装 php，无法执行语法检查")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"PHP语法检查执行失败: {exc}")
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "PHP语法错误").strip()
            raise HTTPException(status_code=400, detail=f"PHP语法检查失败: {msg[:400]}")


@router.post("/upload", response_model=PluginResponse)
def upload_plugin(payload: PluginUpsertRequest, db: Session = Depends(get_db)):
    plugin = db.query(PluginPackage).filter(PluginPackage.plugin_type == DEFAULT_PLUGIN_TYPE).first()
    if plugin:
        raise HTTPException(status_code=400, detail="单插件模式仅允许一个跳转插件，请使用修改功能")
    if payload.version.strip() != DEFAULT_PLUGIN_VERSION:
        raise HTTPException(status_code=400, detail=f"首次上传版本必须为 {DEFAULT_PLUGIN_VERSION}")
    plugin = PluginPackage(
        plugin_type=DEFAULT_PLUGIN_TYPE,
        name=payload.name.strip(),
        owner_username=(payload.owner_username or "admin"),
        current_version=payload.version.strip(),
        config_json=json.dumps(payload.config or {}, ensure_ascii=False),
    )
    db.add(plugin)
    db.commit()
    db.refresh(plugin)
    ver = PluginVersion(
        plugin_id=plugin.id,
        version=plugin.current_version,
        change_log=payload.change_log.strip(),
        config_snapshot_json=plugin.config_json,
        created_by=(payload.created_by or "admin"),
    )
    db.add(ver)
    db.commit()
    log_operation(
        db,
        action="plugin.upload",
        message=f"上传插件: {plugin.name} {plugin.current_version}",
        detail={"plugin_id": plugin.id, "version": plugin.current_version},
    )
    return plugin


@router.get("/", response_model=list[PluginResponse])
def get_plugins(db: Session = Depends(get_db)):
    ensure_default_redirect_plugin(db)
    return db.query(PluginPackage).order_by(PluginPackage.id.desc()).all()


@router.get("/{plugin_id}", response_model=PluginResponse)
def get_plugin(plugin_id: int, db: Session = Depends(get_db)):
    plugin = db.query(PluginPackage).filter(PluginPackage.id == plugin_id).first()
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    return plugin


@router.get("/{plugin_id}/suggest-version")
def get_suggest_version(plugin_id: int, db: Session = Depends(get_db)):
    plugin = db.query(PluginPackage).filter(PluginPackage.id == plugin_id).first()
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    return {"current_version": plugin.current_version, "suggest_version": bump_patch(plugin.current_version)}


@router.put("/{plugin_id}", response_model=PluginResponse)
def update_plugin(plugin_id: int, payload: PluginUpdateRequest, db: Session = Depends(get_db)):
    plugin = db.query(PluginPackage).filter(PluginPackage.id == plugin_id).first()
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")

    before_cfg = _parse_json(plugin.config_json)
    next_cfg = dict(before_cfg)
    if payload.config is not None:
        for k, v in (payload.config or {}).items():
            next_cfg[k] = v
    cfg_changed = payload.config is not None and json.dumps(before_cfg, ensure_ascii=False, sort_keys=True) != json.dumps(next_cfg, ensure_ascii=False, sort_keys=True)
    version_changed = bool(payload.version)

    if cfg_changed or version_changed:
        if not payload.change_log or not str(payload.change_log).strip():
            raise HTTPException(status_code=400, detail="修改配置或版本时，更新内容(change_log)必填")
        next_version = str(payload.version or bump_patch(plugin.current_version)).strip()
        try:
            if not is_version_gt(next_version, plugin.current_version):
                raise HTTPException(status_code=400, detail="版本号必须大于当前版本")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        plugin.current_version = next_version
        plugin.config_json = json.dumps(next_cfg, ensure_ascii=False)
        ver = PluginVersion(
            plugin_id=plugin.id,
            version=next_version,
            change_log=str(payload.change_log).strip(),
            config_snapshot_json=plugin.config_json,
            created_by=(payload.created_by or "admin"),
        )
        db.add(ver)

    if payload.name is not None and str(payload.name).strip():
        plugin.name = str(payload.name).strip()
    if payload.owner_username is not None:
        plugin.owner_username = str(payload.owner_username).strip() or None
    db.add(plugin)
    db.commit()
    db.refresh(plugin)

    log_operation(
        db,
        action="plugin.update",
        message=f"更新插件: {plugin.name} {plugin.current_version}",
        detail={"plugin_id": plugin.id, "version": plugin.current_version, "cfg_changed": cfg_changed, "version_changed": version_changed},
    )
    return plugin


@router.post("/{plugin_id}/upload-php", response_model=PluginResponse)
async def upload_plugin_php(
    plugin_id: int,
    file: UploadFile = File(...),
    version: str = Form(""),
    change_log: str = Form(""),
    created_by: str = Form("admin"),
    db: Session = Depends(get_db),
):
    plugin = db.query(PluginPackage).filter(PluginPackage.id == plugin_id).first()
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    filename = str(file.filename or "").lower()
    if not filename.endswith(".php"):
        raise HTTPException(status_code=400, detail="仅支持上传 .php 文件")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")
    try:
        php_code = raw.decode("utf-8")
    except Exception:
        php_code = raw.decode("utf-8", errors="ignore")
    if not php_code.strip():
        raise HTTPException(status_code=400, detail="PHP代码为空")
    _php_lint_or_raise(php_code)
    if not str(change_log or "").strip():
        raise HTTPException(status_code=400, detail="上传PHP时更新内容(change_log)必填")
    next_version = str(version or bump_patch(plugin.current_version)).strip()
    try:
        if not is_version_gt(next_version, plugin.current_version):
            raise HTTPException(status_code=400, detail="版本号必须大于当前版本")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    cfg = _parse_json(plugin.config_json)
    cfg["php_code"] = php_code
    plugin.current_version = next_version
    plugin.config_json = json.dumps(cfg, ensure_ascii=False)
    db.add(plugin)
    db.add(
        PluginVersion(
            plugin_id=plugin.id,
            version=next_version,
            change_log=str(change_log).strip(),
            config_snapshot_json=plugin.config_json,
            created_by=(created_by or "admin"),
        )
    )
    db.commit()
    db.refresh(plugin)
    log_operation(
        db,
        action="plugin.upload_php",
        message=f"上传并更新PHP代码: {plugin.name} {plugin.current_version}",
        detail={"plugin_id": plugin.id, "version": plugin.current_version, "filename": file.filename},
    )
    return plugin


@router.delete("/{plugin_id}")
def delete_plugin(plugin_id: int, db: Session = Depends(get_db)):
    plugin = db.query(PluginPackage).filter(PluginPackage.id == plugin_id).first()
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    using_count = db.query(SitePluginDeployment).filter(SitePluginDeployment.plugin_id == plugin_id).count()
    if using_count > 0:
        raise HTTPException(status_code=400, detail=f"仍有 {using_count} 个站点绑定该插件，无法删除")
    db.query(PluginVersion).filter(PluginVersion.plugin_id == plugin_id).delete(synchronize_session=False)
    db.delete(plugin)
    db.commit()
    log_operation(db, action="plugin.delete", message=f"删除插件: {plugin.name}", detail={"plugin_id": plugin_id})
    return {"status": "success"}


@router.get("/{plugin_id}/versions", response_model=list[PluginVersionResponse])
def get_plugin_versions(plugin_id: int, db: Session = Depends(get_db)):
    plugin = db.query(PluginPackage).filter(PluginPackage.id == plugin_id).first()
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    return (
        db.query(PluginVersion)
        .filter(PluginVersion.plugin_id == plugin_id)
        .order_by(PluginVersion.id.desc())
        .all()
    )


@router.get("/{plugin_id}/template-code")
def get_plugin_template_code(plugin_id: int, db: Session = Depends(get_db)):
    plugin = db.query(PluginPackage).filter(PluginPackage.id == plugin_id).first()
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    from services.plugin_deploy_service import render_redirect_guard_php

    cfg = _parse_json(plugin.config_json)
    php = str(cfg.get("php_code") or "") or render_redirect_guard_php(cfg)
    return {"plugin_id": plugin_id, "version": plugin.current_version, "code": php}


@router.get("/{plugin_id}/sites", response_model=PluginSitePageResponse)
def get_plugin_sites(
    plugin_id: int,
    page: int = 1,
    page_size: int = 20,
    server_id: Optional[int] = None,
    version: Optional[str] = None,
    db: Session = Depends(get_db),
):
    plugin = db.query(PluginPackage).filter(PluginPackage.id == plugin_id).first()
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 200))

    q = db.query(SitePluginDeployment, Site, Server).join(Site, Site.id == SitePluginDeployment.site_id).join(Server, Server.id == Site.server_id).filter(SitePluginDeployment.plugin_id == plugin_id)
    if server_id:
        q = q.filter(Site.server_id == int(server_id))
    if version:
        q = q.filter(SitePluginDeployment.version == str(version).strip())

    total = q.count()
    rows = (
        q.order_by(SitePluginDeployment.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [
        PluginSiteItem(
            site_id=site.id,
            domain=site.domain,
            server_id=server.id,
            server_name=server.name,
            server_ip=server.main_ip,
            plugin_version=deploy.version,
            enabled=bool(deploy.enabled),
            status=deploy.status,
            error_msg=deploy.error_msg,
            deployed_at=deploy.deployed_at,
        )
        for deploy, site, server in rows
    ]
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    return PluginSitePageResponse(items=items, total=total, page=page, page_size=page_size, total_pages=total_pages)


@router.post("/{plugin_id}/redeploy")
def redeploy_plugin(plugin_id: int, payload: PluginRedeployRequest, db: Session = Depends(get_db)):
    plugin = db.query(PluginPackage).filter(PluginPackage.id == plugin_id).first()
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")

    target_mode = payload.target_mode
    site_ids = sorted({int(x) for x in (payload.site_ids or []) if int(x) > 0})
    if payload.site_id and int(payload.site_id) > 0:
        site_ids.append(int(payload.site_id))
        site_ids = sorted(set(site_ids))
    if target_mode == "single_site" and not site_ids:
        raise HTTPException(status_code=400, detail="single_site 模式必须提供 site_id 或 site_ids")
    if target_mode == "single_server" and not payload.server_id:
        raise HTTPException(status_code=400, detail="single_server 模式必须提供 server_id")

    version = str(payload.version or plugin.current_version).strip()
    if payload.version:
        exists = (
            db.query(PluginVersion)
            .filter(PluginVersion.plugin_id == plugin_id, PluginVersion.version == version)
            .first()
        )
        if not exists:
            raise HTTPException(status_code=400, detail="指定版本不存在")

    task_log = create_task_log(
        db,
        task_type="plugin_redeploy_batch",
        task_name="插件重部署",
        message=f"已入队：插件 {plugin.name} v{version}",
        detail={
            "plugin_id": plugin_id,
            "plugin_name": plugin.name,
            "target_mode": target_mode,
            "site_id": payload.site_id,
            "site_ids": site_ids,
            "server_id": payload.server_id,
            "version": version,
        },
        status="queued",
    )
    task = process_plugin_redeploy_batch.delay(task_log.id, plugin_id, target_mode, payload.site_id, payload.server_id, version, site_ids)
    update_task_log(db, task_log.id, task_ref=task.id)
    log_operation(
        db,
        action="plugin.redeploy.submit",
        message=f"提交插件重部署任务: {plugin.name} v{version}",
        detail={"plugin_id": plugin_id, "target_mode": target_mode, "site_ids": site_ids, "task_log_id": task_log.id, "task_id": task.id},
    )
    return {"status": "success", "task_log_id": task_log.id, "task_id": task.id}

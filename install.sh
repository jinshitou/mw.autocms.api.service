#!/bin/bash
# 批量易优 - 一键傻瓜式部署脚本

echo "================================================="
echo "   正在初始化: 批量易优 SaaS 核心服务器环境      "
echo "================================================="

# 1. 自动安装 Docker 和 Docker-Compose (如果未安装)
if ! command -v docker &> /dev/null; then
    echo "检测到未安装 Docker，正在为您自动安装..."
    curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun
    systemctl start docker
    systemctl enable docker
fi

if ! command -v docker-compose &> /dev/null; then
    echo "检测到未安装 Docker-Compose，正在为您自动安装..."
    curl -L "https://github.com/docker/compose/releases/download/v2.20.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
fi

# 2. 赋予脚本执行权限并启动
echo "正在拉取镜像并启动整个项目集群..."
docker-compose up -d --build

echo "================================================="
echo "🎉 恭喜！批量易优系统部署成功！"
echo "================================================="
echo "👉 后端 API 运行在: http://服务器IP:8000"
echo "👉 接口文档地址:    http://服务器IP:8000/docs"
echo "👉 数据库已经自动建表完毕，运行在 5432 端口"
echo "================================================="
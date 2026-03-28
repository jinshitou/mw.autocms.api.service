-- init.sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    role VARCHAR(20) DEFAULT 'specialist',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE servers (
    id SERIAL PRIMARY KEY,
    owner_id INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    host_ip VARCHAR(50) NOT NULL,
    bt_panel_url VARCHAR(255) NOT NULL,
    bt_api_key VARCHAR(100) NOT NULL,
    alias VARCHAR(100) NOT NULL UNIQUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sites (
    id SERIAL PRIMARY KEY,
    owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    server_id INTEGER REFERENCES servers(id) ON DELETE RESTRICT,
    cms_type VARCHAR(20) NOT NULL,
    domain VARCHAR(100) NOT NULL,
    bind_ip VARCHAR(50) NOT NULL,
    php_version VARCHAR(10) DEFAULT '74',
    admin_path VARCHAR(50) NOT NULL,
    admin_user VARCHAR(50) NOT NULL,
    admin_password VARCHAR(255) NOT NULL,
    db_name VARCHAR(50) NOT NULL,
    db_pass VARCHAR(100) NOT NULL,
    status VARCHAR(20) DEFAULT 'deploying'
);
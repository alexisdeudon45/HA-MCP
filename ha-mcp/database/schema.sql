-- ============================================================
-- HA-MCP Relational Database
-- Base de données : tool
-- Engine : SQLite (dev) / PostgreSQL (prod)
-- ============================================================

-- ============================================================
-- TABLE : tool  (table principale — inventaire des outils MCP)
-- ============================================================
CREATE TABLE tool (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mcp_id          TEXT    NOT NULL,                  -- FK -> mcp.mcp_id
    name            TEXT    NOT NULL,                  -- nom exact (ex: duckduckgo_web_search)
    description     TEXT    NOT NULL,
    capability      TEXT    NOT NULL,                  -- web_search | file_read | reasoning | nlp | ...
    timeout_ms      INTEGER NOT NULL DEFAULT 10000,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),

    FOREIGN KEY (mcp_id) REFERENCES mcp(mcp_id) ON DELETE CASCADE,
    UNIQUE (mcp_id, name)
);

-- ============================================================
-- TABLE : mcp  (registre des serveurs MCP)
-- ============================================================
CREATE TABLE mcp (
    mcp_id          TEXT    PRIMARY KEY,               -- ex: duckduckgo, anthropic_claude
    name            TEXT    NOT NULL,
    version         TEXT    NOT NULL DEFAULT '1.0.0',
    description     TEXT    NOT NULL,
    category        TEXT    NOT NULL CHECK (category IN (
                        'ingestion','structuration','enrichissement',
                        'raisonnement','validation','generation'
                    )),
    is_local        INTEGER NOT NULL DEFAULT 0,        -- 0=cloud, 1=local
    is_free         INTEGER NOT NULL DEFAULT 1,
    homepage_url    TEXT,
    docs_url        TEXT,
    rpm_limit       INTEGER,                           -- rate limit requests/min (NULL = no limit)
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- TABLE : tool_parameter  (paramètres d'entrée de chaque outil)
-- ============================================================
CREATE TABLE tool_parameter (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id         INTEGER NOT NULL,                  -- FK -> tool.id
    name            TEXT    NOT NULL,
    type            TEXT    NOT NULL CHECK (type IN (
                        'string','integer','number','boolean','array','object'
                    )),
    description     TEXT    NOT NULL,
    required        INTEGER NOT NULL DEFAULT 0,        -- 0=optional, 1=required
    default_value   TEXT,                              -- JSON string
    enum_values     TEXT,                              -- JSON array string, NULL si pas enum
    min_value       REAL,
    max_value       REAL,
    max_length      INTEGER,
    position        INTEGER NOT NULL DEFAULT 0,        -- ordre d'affichage

    FOREIGN KEY (tool_id) REFERENCES tool(id) ON DELETE CASCADE
);

-- ============================================================
-- TABLE : mcp_auth  (configuration d'authentification)
-- ============================================================
CREATE TABLE mcp_auth (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mcp_id          TEXT    NOT NULL UNIQUE,           -- FK -> mcp.mcp_id
    required        INTEGER NOT NULL DEFAULT 0,
    auth_type       TEXT    NOT NULL CHECK (auth_type IN (
                        'none','api_key','oauth','bearer_token','basic'
                    )),
    key_name        TEXT,                              -- ex: ANTHROPIC_API_KEY
    key_header      TEXT,                              -- ex: Authorization

    FOREIGN KEY (mcp_id) REFERENCES mcp(mcp_id) ON DELETE CASCADE
);

-- ============================================================
-- TABLE : mcp_probe  (appel de test pour vérifier la dispo)
-- ============================================================
CREATE TABLE mcp_probe (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mcp_id          TEXT    NOT NULL UNIQUE,
    tool_name       TEXT    NOT NULL,                  -- outil à appeler pour le probe
    args_json       TEXT    NOT NULL DEFAULT '{}',     -- args JSON
    timeout_ms      INTEGER NOT NULL DEFAULT 5000,

    FOREIGN KEY (mcp_id) REFERENCES mcp(mcp_id) ON DELETE CASCADE
);

-- ============================================================
-- TABLE : mcp_error_policy  (politique de gestion d'erreurs)
-- ============================================================
CREATE TABLE mcp_error_policy (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    mcp_id              TEXT    NOT NULL UNIQUE,
    retry               INTEGER NOT NULL DEFAULT 1,
    max_retries         INTEGER NOT NULL DEFAULT 2,
    retry_delay_ms      INTEGER NOT NULL DEFAULT 500,
    on_failure          TEXT    NOT NULL CHECK (on_failure IN (
                            'skip','abort_pipeline','degrade','fallback'
                        )),
    fallback_mcp_id     TEXT,                          -- FK -> mcp.mcp_id (nullable)
    degradation_message TEXT,

    FOREIGN KEY (mcp_id)          REFERENCES mcp(mcp_id) ON DELETE CASCADE,
    FOREIGN KEY (fallback_mcp_id) REFERENCES mcp(mcp_id)
);

-- ============================================================
-- TABLE : mcp_pipeline_stage  (à quelles étapes chaque MCP s'active)
-- ============================================================
CREATE TABLE mcp_pipeline_stage (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    mcp_id   TEXT NOT NULL,
    stage    TEXT NOT NULL CHECK (stage IN (
                 '1.1','1.2','1.3','1.4','1.5','1.6',
                 '2.1','2.2','2.3','2.4','2.5','2.55','2.6'
             )),

    FOREIGN KEY (mcp_id) REFERENCES mcp(mcp_id) ON DELETE CASCADE,
    UNIQUE (mcp_id, stage)
);

-- ============================================================
-- TABLE : mcp_capability  (mapping mcp -> capacités)
-- ============================================================
CREATE TABLE mcp_capability (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    mcp_id     TEXT NOT NULL,
    capability TEXT NOT NULL CHECK (capability IN (
                   'ingestion','structuration','enrichissement','raisonnement',
                   'validation','generation','nlp','reasoning',
                   'web_search','web_scrape','file_read','file_write','storage'
               )),

    FOREIGN KEY (mcp_id) REFERENCES mcp(mcp_id) ON DELETE CASCADE,
    UNIQUE (mcp_id, capability)
);

-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX idx_tool_mcp_id       ON tool(mcp_id);
CREATE INDEX idx_tool_capability   ON tool(capability);
CREATE INDEX idx_param_tool_id     ON tool_parameter(tool_id);
CREATE INDEX idx_param_required    ON tool_parameter(required);
CREATE INDEX idx_stage_mcp_id      ON mcp_pipeline_stage(mcp_id);
CREATE INDEX idx_stage_stage       ON mcp_pipeline_stage(stage);
CREATE INDEX idx_capability_mcp_id ON mcp_capability(mcp_id);

-- ============================================================
-- SEED DATA — MCPs inspectés via tools/list
-- ============================================================

-- ---- MCPs ----
INSERT INTO mcp VALUES ('duckduckgo',         'DuckDuckGo Search',    '1.0.0', 'Recherche web publique sans clé API',                                    'enrichissement', 0, 1, NULL, 'https://pypi.org/project/ddgs/', 30,  datetime('now'));
INSERT INTO mcp VALUES ('sequential-thinking','Sequential Thinking',  '0.2.0', 'Raisonnement séquentiel structuré révisable',                            'raisonnement',   1, 1, NULL, NULL,                            NULL, datetime('now'));
INSERT INTO mcp VALUES ('playwright',         'Playwright Browser',   '1.60.0','Contrôle navigateur Chromium headless pour scraping web',                 'enrichissement', 1, 1, NULL, 'https://playwright.dev',        NULL, datetime('now'));
INSERT INTO mcp VALUES ('filesystem-pipeline','Filesystem Pipeline',  '1.0.0', 'Lecture/écriture fichiers locaux avec tail/head/media',                   'ingestion',      1, 1, NULL, NULL,                            NULL, datetime('now'));
INSERT INTO mcp VALUES ('anthropic_claude',   'Anthropic Claude API', '1.0.0', 'LLM Claude — structuration, analyse, NLP, génération',                    'raisonnement',   0, 0, 'https://anthropic.com', 'https://docs.anthropic.com', 60, datetime('now'));
INSERT INTO mcp VALUES ('openai_gpt',         'OpenAI GPT API',       '1.0.0', 'LLM GPT — alternative à Claude',                                          'raisonnement',   0, 0, NULL, 'https://platform.openai.com/docs', 60, datetime('now'));
INSERT INTO mcp VALUES ('google_gemini',      'Google Gemini',        '1.0.0', 'LLM Gemini — alternative multimodale',                                    'raisonnement',   0, 0, NULL, 'https://ai.google.dev/docs',    60,  datetime('now'));
INSERT INTO mcp VALUES ('mistral_ai',         'Mistral AI',           '1.0.0', 'LLM Mistral — option souveraine européenne RGPD',                         'raisonnement',   0, 0, 'https://mistral.ai', 'https://docs.mistral.ai', 60, datetime('now'));
INSERT INTO mcp VALUES ('huggingface',        'Hugging Face',         '1.0.0', 'NER et classification via Inference API Hugging Face',                    'structuration',  0, 0, 'https://huggingface.co', 'https://huggingface.co/docs/api-inference', 30, datetime('now'));
INSERT INTO mcp VALUES ('notion_api',         'Notion',               '1.0.0', 'Stockage et lecture analyses dans Notion',                                'enrichissement', 0, 0, NULL, 'https://developers.notion.com', 30, datetime('now'));
INSERT INTO mcp VALUES ('google_drive',       'Google Drive',         '1.0.0', 'Lecture PDFs depuis Google Drive sans upload manuel',                     'ingestion',      0, 1, NULL, 'https://developers.google.com/drive/api', NULL, datetime('now'));
INSERT INTO mcp VALUES ('local_pdf',          'PDF Extractor',        '1.0.0', 'Extraction texte PDF via PyMuPDF avec checksum SHA-256',                  'ingestion',      1, 1, NULL, 'https://pymupdf.readthedocs.io', NULL, datetime('now'));

-- ---- Auth ----
INSERT INTO mcp_auth (mcp_id, required, auth_type, key_name,         key_header)      VALUES ('duckduckgo',         0, 'none',    NULL,                  NULL);
INSERT INTO mcp_auth (mcp_id, required, auth_type, key_name,         key_header)      VALUES ('sequential-thinking',0, 'none',    NULL,                  NULL);
INSERT INTO mcp_auth (mcp_id, required, auth_type, key_name,         key_header)      VALUES ('playwright',         0, 'none',    NULL,                  NULL);
INSERT INTO mcp_auth (mcp_id, required, auth_type, key_name,         key_header)      VALUES ('filesystem-pipeline',0, 'none',    NULL,                  NULL);
INSERT INTO mcp_auth (mcp_id, required, auth_type, key_name,         key_header)      VALUES ('anthropic_claude',   1, 'api_key', 'ANTHROPIC_API_KEY',   'x-api-key');
INSERT INTO mcp_auth (mcp_id, required, auth_type, key_name,         key_header)      VALUES ('openai_gpt',         1, 'api_key', 'OPENAI_API_KEY',      'Authorization');
INSERT INTO mcp_auth (mcp_id, required, auth_type, key_name,         key_header)      VALUES ('google_gemini',      1, 'api_key', 'GOOGLE_API_KEY',      'x-goog-api-key');
INSERT INTO mcp_auth (mcp_id, required, auth_type, key_name,         key_header)      VALUES ('mistral_ai',         1, 'api_key', 'MISTRAL_API_KEY',     'Authorization');
INSERT INTO mcp_auth (mcp_id, required, auth_type, key_name,         key_header)      VALUES ('huggingface',        1, 'api_key', 'HF_API_KEY',          'Authorization');
INSERT INTO mcp_auth (mcp_id, required, auth_type, key_name,         key_header)      VALUES ('notion_api',         1, 'api_key', 'NOTION_API_KEY',      'Authorization');
INSERT INTO mcp_auth (mcp_id, required, auth_type, key_name,         key_header)      VALUES ('google_drive',       1, 'oauth',   'GOOGLE_DRIVE_TOKEN',  'Authorization');
INSERT INTO mcp_auth (mcp_id, required, auth_type, key_name,         key_header)      VALUES ('local_pdf',          0, 'none',    NULL,                  NULL);

-- ---- Probes (source: tools/list réel) ----
INSERT INTO mcp_probe (mcp_id, tool_name, args_json, timeout_ms) VALUES ('duckduckgo',         'duckduckgo_web_search',  '{"query":"test","count":1}',                                                                    5000);
INSERT INTO mcp_probe (mcp_id, tool_name, args_json, timeout_ms) VALUES ('sequential-thinking','sequentialthinking',     '{"thought":"probe","nextThoughtNeeded":false,"thoughtNumber":1,"totalThoughts":1}',             3000);
INSERT INTO mcp_probe (mcp_id, tool_name, args_json, timeout_ms) VALUES ('playwright',         'browser_navigate',       '{"url":"about:blank"}',                                                                         8000);
INSERT INTO mcp_probe (mcp_id, tool_name, args_json, timeout_ms) VALUES ('filesystem-pipeline','list_allowed_directories','{}',                                                                                            1000);
INSERT INTO mcp_probe (mcp_id, tool_name, args_json, timeout_ms) VALUES ('anthropic_claude',   'chat_completion',        '{"model":"claude-haiku-4-5-20251001","system":"Reply ok.","prompt":"ping","max_tokens":5}',    8000);
INSERT INTO mcp_probe (mcp_id, tool_name, args_json, timeout_ms) VALUES ('local_pdf',          'extract_pdf_text',       '{"path":"/dev/null"}',                                                                          2000);

-- ---- Error policies ----
INSERT INTO mcp_error_policy (mcp_id, retry, max_retries, retry_delay_ms, on_failure, fallback_mcp_id, degradation_message) VALUES
  ('duckduckgo',         1, 2, 1000, 'degrade',         NULL,              'DuckDuckGo inaccessible — enrichissement web désactivé');
INSERT INTO mcp_error_policy (mcp_id, retry, max_retries, retry_delay_ms, on_failure, fallback_mcp_id, degradation_message) VALUES
  ('sequential-thinking',0, 0, 0,    'degrade',         NULL,              'Sequential Thinking indisponible — raisonnement direct Claude');
INSERT INTO mcp_error_policy (mcp_id, retry, max_retries, retry_delay_ms, on_failure, fallback_mcp_id, degradation_message) VALUES
  ('playwright',         1, 1, 2000, 'degrade',         NULL,              'Playwright indisponible — DuckDuckGo uniquement');
INSERT INTO mcp_error_policy (mcp_id, retry, max_retries, retry_delay_ms, on_failure, fallback_mcp_id, degradation_message) VALUES
  ('filesystem-pipeline',0, 0, 0,    'abort_pipeline',  NULL,              'Filesystem inaccessible — permissions à vérifier');
INSERT INTO mcp_error_policy (mcp_id, retry, max_retries, retry_delay_ms, on_failure, fallback_mcp_id, degradation_message) VALUES
  ('anthropic_claude',   1, 2, 2000, 'abort_pipeline',  NULL,              'ANTHROPIC_API_KEY manquante — pipeline impossible');
INSERT INTO mcp_error_policy (mcp_id, retry, max_retries, retry_delay_ms, on_failure, fallback_mcp_id, degradation_message) VALUES
  ('openai_gpt',         1, 2, 2000, 'fallback',        'anthropic_claude','OpenAI indisponible — fallback Claude');
INSERT INTO mcp_error_policy (mcp_id, retry, max_retries, retry_delay_ms, on_failure, fallback_mcp_id, degradation_message) VALUES
  ('google_gemini',      1, 2, 2000, 'fallback',        'anthropic_claude','Gemini indisponible — fallback Claude');
INSERT INTO mcp_error_policy (mcp_id, retry, max_retries, retry_delay_ms, on_failure, fallback_mcp_id, degradation_message) VALUES
  ('mistral_ai',         1, 2, 2000, 'fallback',        'anthropic_claude','Mistral indisponible — fallback Claude');
INSERT INTO mcp_error_policy (mcp_id, retry, max_retries, retry_delay_ms, on_failure, fallback_mcp_id, degradation_message) VALUES
  ('huggingface',        1, 2, 2000, 'degrade',         NULL,              'HuggingFace indisponible — NER via Claude uniquement');
INSERT INTO mcp_error_policy (mcp_id, retry, max_retries, retry_delay_ms, on_failure, fallback_mcp_id, degradation_message) VALUES
  ('notion_api',         1, 2, 1000, 'degrade',         NULL,              'Notion indisponible — stockage désactivé');
INSERT INTO mcp_error_policy (mcp_id, retry, max_retries, retry_delay_ms, on_failure, fallback_mcp_id, degradation_message) VALUES
  ('local_pdf',          0, 0, 0,    'abort_pipeline',  NULL,              'PDF Extractor indisponible — vérifier PyMuPDF');

-- ---- Tools (source: tools/list réel pour duckduckgo, sequential-thinking, playwright, filesystem-pipeline) ----
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('duckduckgo','duckduckgo_web_search','Performs a web search using DuckDuckGo. Max 20 results.','web_search',10000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('sequential-thinking','sequentialthinking','Dynamic reflective problem-solving through sequential thoughts.','reasoning',5000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_navigate','Navigate to a URL',                       'web_scrape',15000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_snapshot','Accessibility snapshot of current page',  'web_scrape',10000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_take_screenshot','Screenshot of current page',       'web_scrape',10000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_click','Click a web element',                        'web_scrape',5000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_fill_form','Fill out a form with multiple fields',   'web_scrape',10000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_evaluate','Execute JavaScript in browser',           'web_scrape',10000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_navigate_back','Go back in browser history',         'web_scrape',5000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_network_requests','List network requests of page',   'web_scrape',5000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_console_messages','Get browser console messages',    'web_scrape',3000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_close','Close the browser',                          'web_scrape',3000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_resize','Resize browser window',                     'web_scrape',3000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_type','Type text into element',                      'web_scrape',5000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_press_key','Press a keyboard key',                   'web_scrape',3000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_hover','Hover over an element',                      'web_scrape',3000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_drag','Drag an element to target',                   'web_scrape',5000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_select_option','Select option in dropdown',          'web_scrape',5000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_handle_dialog','Handle browser alert/confirm/prompt','web_scrape',5000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_file_upload','Upload files via chooser dialog',      'web_scrape',10000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_tabs','Manage browser tabs',                          'web_scrape',5000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_wait_for','Wait for condition/text/time',            'web_scrape',30000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('playwright','browser_run_code','Run arbitrary Playwright code',           'web_scrape',30000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('filesystem-pipeline','read_file','Read file with optional head/tail',     'file_read',5000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('filesystem-pipeline','read_text_file','Read text file with encoding',     'file_read',5000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('filesystem-pipeline','read_media_file','Read media file as base64',       'file_read',10000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('filesystem-pipeline','read_multiple_files','Read multiple files at once', 'file_read',10000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('filesystem-pipeline','write_file','Write content to file',                'file_write',5000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('filesystem-pipeline','edit_file','Edit file by replacing text snippets', 'file_write',5000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('filesystem-pipeline','create_directory','Create directory recursively',   'file_write',2000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('filesystem-pipeline','list_directory','List directory contents',          'file_read',3000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('filesystem-pipeline','list_directory_with_sizes','List with file sizes',  'file_read',3000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('filesystem-pipeline','directory_tree','Recursive directory tree',         'file_read',5000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('filesystem-pipeline','move_file','Move or rename a file',                 'file_write',3000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('filesystem-pipeline','search_files','Search files by pattern',            'file_read',10000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('filesystem-pipeline','get_file_info','Get file metadata',                 'file_read',2000);
INSERT INTO tool (mcp_id, name, description, capability, timeout_ms) VALUES ('filesystem-pipeline','list_allowed_directories','List allowed dirs',      'file_read',1000);

-- ---- Tool parameters (source: tools/list réel) ----
-- duckduckgo_web_search
INSERT INTO tool_parameter (tool_id, name, type, description, required, default_value, enum_values, max_length, position)
SELECT t.id, 'query',      'string', 'Search query (max 400 chars)', 1, NULL,         NULL,                          400, 0 FROM tool t WHERE t.mcp_id='duckduckgo' AND t.name='duckduckgo_web_search';
INSERT INTO tool_parameter (tool_id, name, type, description, required, default_value, min_value, max_value, position)
SELECT t.id, 'count',      'number', 'Number of results (1-20)',     0, '10',         1,          20,         1 FROM tool t WHERE t.mcp_id='duckduckgo' AND t.name='duckduckgo_web_search';
INSERT INTO tool_parameter (tool_id, name, type, description, required, default_value, enum_values, position)
SELECT t.id, 'safeSearch', 'string', 'SafeSearch level',             0, '"moderate"', '["strict","moderate","off"]', 2 FROM tool t WHERE t.mcp_id='duckduckgo' AND t.name='duckduckgo_web_search';

-- sequentialthinking
INSERT INTO tool_parameter (tool_id, name, type, description, required, position)
SELECT t.id, 'thought',           'string',  'Current thinking step',             1, 0 FROM tool t WHERE t.mcp_id='sequential-thinking' AND t.name='sequentialthinking';
INSERT INTO tool_parameter (tool_id, name, type, description, required, position)
SELECT t.id, 'nextThoughtNeeded', 'boolean', 'Whether another thought is needed', 1, 1 FROM tool t WHERE t.mcp_id='sequential-thinking' AND t.name='sequentialthinking';
INSERT INTO tool_parameter (tool_id, name, type, description, required, min_value, position)
SELECT t.id, 'thoughtNumber',     'integer', 'Current thought number',            1, 1, 2 FROM tool t WHERE t.mcp_id='sequential-thinking' AND t.name='sequentialthinking';
INSERT INTO tool_parameter (tool_id, name, type, description, required, min_value, position)
SELECT t.id, 'totalThoughts',     'integer', 'Estimated total thoughts needed',   1, 1, 3 FROM tool t WHERE t.mcp_id='sequential-thinking' AND t.name='sequentialthinking';
INSERT INTO tool_parameter (tool_id, name, type, description, required, position)
SELECT t.id, 'isRevision',        'boolean', 'Whether this revises previous thinking', 0, 4 FROM tool t WHERE t.mcp_id='sequential-thinking' AND t.name='sequentialthinking';
INSERT INTO tool_parameter (tool_id, name, type, description, required, min_value, position)
SELECT t.id, 'revisesThought',    'integer', 'Which thought number is being reconsidered', 0, 1, 5 FROM tool t WHERE t.mcp_id='sequential-thinking' AND t.name='sequentialthinking';

-- browser_navigate
INSERT INTO tool_parameter (tool_id, name, type, description, required, position)
SELECT t.id, 'url', 'string', 'The URL to navigate to', 1, 0 FROM tool t WHERE t.mcp_id='playwright' AND t.name='browser_navigate';

-- browser_resize
INSERT INTO tool_parameter (tool_id, name, type, description, required, position)
SELECT t.id, 'width',  'number', 'Width of the browser window',  1, 0 FROM tool t WHERE t.mcp_id='playwright' AND t.name='browser_resize';
INSERT INTO tool_parameter (tool_id, name, type, description, required, position)
SELECT t.id, 'height', 'number', 'Height of the browser window', 1, 1 FROM tool t WHERE t.mcp_id='playwright' AND t.name='browser_resize';

-- read_file
INSERT INTO tool_parameter (tool_id, name, type, description, required, position)
SELECT t.id, 'path', 'string', 'File path to read', 1, 0 FROM tool t WHERE t.mcp_id='filesystem-pipeline' AND t.name='read_file';
INSERT INTO tool_parameter (tool_id, name, type, description, required, position)
SELECT t.id, 'tail', 'number', 'Return only last N lines', 0, 1 FROM tool t WHERE t.mcp_id='filesystem-pipeline' AND t.name='read_file';
INSERT INTO tool_parameter (tool_id, name, type, description, required, position)
SELECT t.id, 'head', 'number', 'Return only first N lines', 0, 2 FROM tool t WHERE t.mcp_id='filesystem-pipeline' AND t.name='read_file';

-- write_file
INSERT INTO tool_parameter (tool_id, name, type, description, required, position)
SELECT t.id, 'path',    'string', 'Destination file path', 1, 0 FROM tool t WHERE t.mcp_id='filesystem-pipeline' AND t.name='write_file';
INSERT INTO tool_parameter (tool_id, name, type, description, required, position)
SELECT t.id, 'content', 'string', 'Content to write',      1, 1 FROM tool t WHERE t.mcp_id='filesystem-pipeline' AND t.name='write_file';

-- ---- Pipeline stages ----
INSERT INTO mcp_pipeline_stage (mcp_id, stage) VALUES ('duckduckgo','1.5'),('duckduckgo','2.1');
INSERT INTO mcp_pipeline_stage (mcp_id, stage) VALUES ('sequential-thinking','1.3'),('sequential-thinking','2.2'),('sequential-thinking','2.4');
INSERT INTO mcp_pipeline_stage (mcp_id, stage) VALUES ('playwright','1.5'),('playwright','2.1');
INSERT INTO mcp_pipeline_stage (mcp_id, stage) VALUES ('filesystem-pipeline','1.1'),('filesystem-pipeline','1.6'),('filesystem-pipeline','2.6');
INSERT INTO mcp_pipeline_stage (mcp_id, stage) VALUES ('anthropic_claude','1.2'),('anthropic_claude','1.3'),('anthropic_claude','2.1'),('anthropic_claude','2.2'),('anthropic_claude','2.4'),('anthropic_claude','2.55'),('anthropic_claude','2.6');
INSERT INTO mcp_pipeline_stage (mcp_id, stage) VALUES ('local_pdf','1.1');

-- ---- Capabilities ----
INSERT INTO mcp_capability (mcp_id, capability) VALUES ('duckduckgo','enrichissement'),('duckduckgo','web_search');
INSERT INTO mcp_capability (mcp_id, capability) VALUES ('sequential-thinking','raisonnement'),('sequential-thinking','reasoning');
INSERT INTO mcp_capability (mcp_id, capability) VALUES ('playwright','enrichissement'),('playwright','web_scrape');
INSERT INTO mcp_capability (mcp_id, capability) VALUES ('filesystem-pipeline','ingestion'),('filesystem-pipeline','file_read'),('filesystem-pipeline','file_write');
INSERT INTO mcp_capability (mcp_id, capability) VALUES ('anthropic_claude','raisonnement'),('anthropic_claude','nlp'),('anthropic_claude','structuration'),('anthropic_claude','generation');
INSERT INTO mcp_capability (mcp_id, capability) VALUES ('huggingface','structuration'),('huggingface','nlp');
INSERT INTO mcp_capability (mcp_id, capability) VALUES ('notion_api','enrichissement'),('notion_api','storage');
INSERT INTO mcp_capability (mcp_id, capability) VALUES ('local_pdf','ingestion');

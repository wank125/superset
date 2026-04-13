const express = require('express');
const jwt = require('jsonwebtoken');
const cors = require('cors');
const axios = require('axios');

const app = express();
const PORT = 5000;

// ============================================
// Configuration - 与 Superset 配置保持一致
// ============================================
const SUPERSET_DOMAIN = 'http://localhost:8088';
const JWT_SECRET = process.env.JWT_SECRET || 'superset-embedded-secret-key-change-in-production';
const JWT_AUDIENCE = 'superset';
const JWT_EXP_SECONDS = 86400; // 24 hours
const API_KEY = process.env.TOKEN_SERVICE_API_KEY; // Required for /api/guest-token

// Middleware
app.use(cors({
    origin: '*',
    credentials: true
}));
app.use(express.json());
app.use(express.static('.')); // 提供静态文件

// ============================================
// 方式 1: 直接生成 JWT Token (推荐用于开发)
// ============================================
function generateGuestToken(dashboardId, userId = 'embedded_user') {
    const now = Math.floor(Date.now() / 1000);
    
    const payload = {
        user: {
            username: userId,
            first_name: 'Embedded',
            last_name: 'User'
        },
        resources: [{
            type: 'dashboard',
            id: dashboardId
        }],
        rls_rules: [], // 可以添加行级安全规则
        iat: now,
        exp: now + JWT_EXP_SECONDS,
        aud: JWT_AUDIENCE,
        type: 'guest'
    };
    
    return jwt.sign(payload, JWT_SECRET);
}

// ============================================
// 方式 2: 通过 Superset API 获取 Token
// ============================================
async function getGuestTokenFromSuperset(dashboardId, userId = 'embedded_user') {
    try {
        // 注意: 这需要先登录 Superset 获取 access_token
        // 这里仅作为示例，实际使用需要实现完整的认证流程
        const response = await axios.post(
            `${SUPERSET_DOMAIN}/api/v1/security/guest_token`,
            {
                user: {
                    username: userId,
                    first_name: 'Embedded',
                    last_name: 'User'
                },
                resources: [{
                    type: 'dashboard',
                    id: dashboardId
                }],
                rls: []
            },
            {
                headers: {
                    'Content-Type': 'application/json',
                    // 'Authorization': `Bearer ${ACCESS_TOKEN}` // 需要有效的 access token
                }
            }
        );
        
        return response.data.token;
    } catch (error) {
        console.error('Error getting token from Superset:', error.message);
        throw error;
    }
}

// ============================================
// API Endpoints
// ============================================

// 生成 Guest Token (使用方式 1 - 直接生成 JWT)
// Requires API key authentication via X-API-Key header
app.post('/api/guest-token', (req, res) => {
    // Authenticate request — API key is mandatory
    const apiKey = req.headers['x-api-key'];
    if (!API_KEY) {
        console.error('FATAL: TOKEN_SERVICE_API_KEY not set. Refusing to serve unauthenticated requests.');
        return res.status(503).json({ error: 'Server misconfigured: API key not set' });
    }
    if (!apiKey || apiKey !== API_KEY) {
        return res.status(401).json({ error: 'Invalid or missing API key' });
    }

    try {
        const { dashboardId, userId } = req.body;
        
        if (!dashboardId) {
            return res.status(400).json({ 
                error: 'dashboardId is required' 
            });
        }
        
        console.log(`Generating guest token for dashboard: ${dashboardId}, user: ${userId || 'embedded_user'}`);
        
        const token = generateGuestToken(dashboardId, userId);
        
        res.json({ 
            token,
            expiresIn: JWT_EXP_SECONDS,
            message: 'Token generated successfully'
        });
    } catch (error) {
        console.error('Error generating token:', error);
        res.status(500).json({ 
            error: 'Failed to generate token',
            details: error.message 
        });
    }
});

// 健康检查
app.get('/health', (req, res) => {
    res.json({ 
        status: 'ok',
        service: 'Superset Guest Token Service',
        timestamp: new Date().toISOString()
    });
});

// 获取 Superset 健康状态
app.get('/api/superset-health', async (req, res) => {
    try {
        const response = await axios.get(`${SUPERSET_DOMAIN}/health`);
        res.json({ 
            status: 'ok',
            superset: response.data 
        });
    } catch (error) {
        res.status(500).json({ 
            status: 'error',
            message: 'Superset is not accessible',
            error: error.message 
        });
    }
});

// 测试页面
app.get('/', (req, res) => {
    res.send(`
        <!DOCTYPE html>
        <html>
        <head>
            <title>Guest Token Service</title>
            <style>
                body { font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }
                h1 { color: #333; }
                .endpoint { background: #f5f5f5; padding: 15px; margin: 10px 0; border-radius: 5px; }
                code { background: #e0e0e0; padding: 2px 5px; border-radius: 3px; }
                .success { color: #28a745; }
            </style>
        </head>
        <body>
            <h1>🎫 Superset Guest Token Service</h1>
            <p class="success">✓ Service is running on port ${PORT}</p>
            
            <h2>Available Endpoints:</h2>
            
            <div class="endpoint">
                <h3>POST /api/guest-token</h3>
                <p>Generate a guest token for embedded dashboards</p>
                <p><strong>Body:</strong></p>
                <pre><code>{
  "dashboardId": "your-dashboard-uuid",
  "userId": "optional-user-id"
}</code></pre>
            </div>
            
            <div class="endpoint">
                <h3>GET /health</h3>
                <p>Check service health status</p>
            </div>
            
            <div class="endpoint">
                <h3>GET /api/superset-health</h3>
                <p>Check Superset connection status</p>
            </div>
            
            <h2>Configuration:</h2>
            <ul>
                <li>Superset Domain: <code>${SUPERSET_DOMAIN}</code></li>
                <li>JWT Audience: <code>${JWT_AUDIENCE}</code></li>
                <li>Token Expiration: <code>${JWT_EXP_SECONDS}s (${JWT_EXP_SECONDS/3600}h)</code></li>
            </ul>
            
            <h2>Next Steps:</h2>
            <ol>
                <li>Open <a href="/test_embedded.html">test_embedded.html</a> to test embedded dashboards</li>
                <li>Make sure Superset is running on ${SUPERSET_DOMAIN}</li>
                <li>Get a valid dashboard UUID from Superset</li>
            </ol>
        </body>
        </html>
    `);
});

// ============================================
// Start Server
// ============================================
app.listen(PORT, () => {
    console.log('='.repeat(50));
    console.log('🚀 Superset Guest Token Service Started');
    console.log('='.repeat(50));
    console.log(`📍 Server running at: http://localhost:${PORT}`);
    console.log(`🔗 Superset domain: ${SUPERSET_DOMAIN}`);
    console.log(`🎫 JWT Audience: ${JWT_AUDIENCE}`);
    console.log(`⏱️  Token expiration: ${JWT_EXP_SECONDS}s`);
    console.log(`🔐 API Key auth: ${API_KEY ? 'enabled' : 'DISABLED (set TOKEN_SERVICE_API_KEY)'}`);
    console.log('='.repeat(50));
    console.log('\n📝 Available endpoints:');
    console.log(`   POST http://localhost:${PORT}/api/guest-token`);
    console.log(`   GET  http://localhost:${PORT}/health`);
    console.log(`   GET  http://localhost:${PORT}/api/superset-health`);
    console.log('\n🧪 Test page:');
    console.log(`   http://localhost:${PORT}/test_embedded.html`);
    console.log('\n✨ Ready to generate guest tokens!\n');
});

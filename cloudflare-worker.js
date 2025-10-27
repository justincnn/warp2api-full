/**
 * Cloudflare Worker 脚本 - 匿名用户创建代理
 *
 * 使用 CF Worker 的 IP 来绕过 Warp 的 IP 限制
 * 访问 Worker URL 即可获取匿名用户 token
 */

// Warp API 配置
const WARP_CONFIG = {
  ANON_GQL_URL: "https://app.warp.dev/graphql/v2?op=CreateAnonymousUser",
  IDENTITY_TOOLKIT_BASE: "https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken",
  REFRESH_URL: "https://app.warp.dev/proxy/token?key=AIzaSyBdy3O3S9hrdayLJxJ7mriBR4qgUaUygAs"
};

/**
 * 生成固定的浏览器头部（对应 Python 代码的固定配置）
 */
function generateHeaders() {
  return {
    "accept-encoding": "gzip, br",
    "content-type": "application/json",
    "x-warp-client-version": "0.2024.01.09.08.02.stable_02",
    "x-warp-os-category": "MACOS",
    "x-warp-os-name": "macOS",
    "x-warp-os-version": "14.2.1"
  };
}

/**
 * 生成固定的 GraphQL 变量（对应 Python 代码的固定配置）
 */
function generateVariables() {
  return {
    input: {
      anonymousUserType: "NATIVE_CLIENT_ANONYMOUS_USER_FEATURE_GATED",
      expirationType: "NO_EXPIRATION",
      referralCode: null
    },
    requestContext: {
      clientContext: {
        version: "0.2024.01.09.08.02.stable_02"
      },
      osContext: {
        category: "MACOS",
        linuxKernelVersion: null,
        name: "macOS",
        version: "14.2.1"
      }
    }
  };
}

/**
 * 创建匿名用户
 */
async function createAnonymousUser() {
  console.log("Creating anonymous user...");

  const headers = generateHeaders();
  const variables = generateVariables();

  const query = `
    mutation CreateAnonymousUser($input: CreateAnonymousUserInput!, $requestContext: RequestContext!) {
      createAnonymousUser(input: $input, requestContext: $requestContext) {
        __typename
        ... on CreateAnonymousUserOutput {
          expiresAt
          anonymousUserType
          firebaseUid
          idToken
          isInviteValid
          responseContext { serverVersion }
        }
        ... on UserFacingError {
          error { __typename message }
          responseContext { serverVersion }
        }
      }
    }
  `;

  const body = {
    query: query,
    variables: variables,
    operationName: "CreateAnonymousUser"
  };

  console.log(`Using Client Version: ${variables.requestContext.clientContext.version}`);

  try {
    const response = await fetch(WARP_CONFIG.ANON_GQL_URL, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(body)
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error(`CreateAnonymousUser failed: HTTP ${response.status}`);
      console.error(`Response: ${errorText.substring(0, 500)}`);
      throw new Error(`CreateAnonymousUser failed: HTTP ${response.status} ${errorText.substring(0, 200)}`);
    }

    const data = await response.json();
    console.log("Anonymous user created successfully");
    return data;

  } catch (error) {
    console.error(`Error creating anonymous user: ${error.message}`);
    throw error;
  }
}

/**
 * 使用 ID token 交换 refresh token
 */
async function exchangeIdTokenForRefreshToken(idToken) {
  console.log("Exchanging ID token for refresh token...");

  const headers = generateHeaders();
  headers["content-type"] = "application/x-www-form-urlencoded";

  const formData = new URLSearchParams({
    returnSecureToken: "true",
    token: idToken
  });

  try {
    const response = await fetch(WARP_CONFIG.IDENTITY_TOOLKIT_BASE + "?key=AIzaSyBdy3O3S9hrdayLJxJ7mriBR4qgUaUygAs", {
      method: 'POST',
      headers: headers,
      body: formData.toString()
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error(`signInWithCustomToken failed: HTTP ${response.status}`);
      console.error(`Response: ${errorText.substring(0, 500)}`);
      throw new Error(`signInWithCustomToken failed: HTTP ${response.status} ${errorText.substring(0, 200)}`);
    }

    const data = await response.json();
    console.log("ID token exchange successful");
    return data;

  } catch (error) {
    console.error(`Error exchanging ID token: ${error.message}`);
    throw error;
  }
}

/**
 * 获取访问令牌
 */
async function acquireAccessToken(refreshToken) {
  console.log("Acquiring access token...");

  const headers = generateHeaders();
  headers["content-type"] = "application/x-www-form-urlencoded";
  headers["accept"] = "*/*";

  const payload = `grant_type=refresh_token&refresh_token=${refreshToken}`;
  headers["content-length"] = payload.length.toString();

  try {
    const response = await fetch(WARP_CONFIG.REFRESH_URL, {
      method: 'POST',
      headers: headers,
      body: payload
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error(`Acquire access_token failed: HTTP ${response.status}`);
      console.error(`Response: ${errorText.substring(0, 500)}`);
      throw new Error(`Acquire access_token failed: HTTP ${response.status} ${errorText.substring(0, 200)}`);
    }

    const data = await response.json();
    console.log("Access token acquired successfully");
    return data;

  } catch (error) {
    console.error(`Error acquiring access token: ${error.message}`);
    throw error;
  }
}

/**
 * 完整的匿名访问令牌获取流程
 */
async function getAnonymousAccessToken() {
  try {
    console.log("Starting anonymous access token acquisition...");

    // 1. 创建匿名用户
    const userData = await createAnonymousUser();
    const createUserData = userData?.data?.createAnonymousUser;

    if (!createUserData?.idToken) {
      throw new Error(`CreateAnonymousUser did not return idToken: ${JSON.stringify(userData)}`);
    }

    const idToken = createUserData.idToken;
    console.log(`Got ID token: ${idToken.substring(0, 50)}...`);

    // 2. 交换 refresh token
    const signinData = await exchangeIdTokenForRefreshToken(idToken);
    const refreshToken = signinData?.refreshToken;

    if (!refreshToken) {
      throw new Error(`signInWithCustomToken did not return refreshToken: ${JSON.stringify(signinData)}`);
    }

    console.log(`Got refresh token: ${refreshToken.substring(0, 50)}...`);

    // 3. 获取访问令牌
    const tokenData = await acquireAccessToken(refreshToken);
    const accessToken = tokenData?.access_token;

    if (!accessToken) {
      throw new Error(`No access_token in response: ${JSON.stringify(tokenData)}`);
    }

    console.log(`Got access token: ${accessToken.substring(0, 50)}...`);

    return {
      success: true,
      accessToken: accessToken,
      refreshToken: refreshToken,
      idToken: idToken,
      userData: createUserData,
      timestamp: new Date().toISOString()
    };

  } catch (error) {
    console.error(`Full flow failed: ${error.message}`);
    return {
      success: false,
      error: error.message,
      timestamp: new Date().toISOString()
    };
  }
}

/**
 * Cloudflare Worker 主处理函数
 */
export default {
  async fetch(request, env, ctx) {
    // 添加 CORS 头
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Content-Type': 'application/json'
    };

    // 处理 OPTIONS 请求（CORS 预检）
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    try {
      const url = new URL(request.url);

      // 根据路径提供不同的功能
      if (url.pathname === '/') {
        // 主页面，显示使用说明
        const html = `
          <!DOCTYPE html>
          <html>
          <head>
            <title>Warp Anonymous Token Service</title>
            <style>
              body { font-family: Arial, sans-serif; margin: 40px; }
              .endpoint { background: #f5f5f5; padding: 10px; margin: 10px 0; border-radius: 5px; }
              .success { color: green; }
              .error { color: red; }
            </style>
          </head>
          <body>
            <h1>🚀 Warp Anonymous Token Service</h1>
            <p>使用 Cloudflare Worker 获取 Warp 匿名访问令牌</p>

            <h2>📡 API 端点</h2>
            <div class="endpoint">
              <strong>GET /token</strong> - 获取完整的访问令牌（推荐）
            </div>
            <div class="endpoint">
              <strong>GET /create</strong> - 仅创建匿名用户（返回 ID Token）
            </div>
            <div class="endpoint">
              <strong>GET /health</strong> - 健康检查
            </div>

            <h2>🔧 使用示例</h2>
            <pre>
# 获取访问令牌
curl ${url.origin}/token

# 仅创建匿名用户
curl ${url.origin}/create

# 健康检查
curl ${url.origin}/health
            </pre>

            <p><em>⚡ 由 Cloudflare Workers 提供服务</em></p>
          </body>
          </html>
        `;

        return new Response(html, {
          headers: { ...corsHeaders, 'Content-Type': 'text/html' }
        });

      } else if (url.pathname === '/token') {
        // 获取完整的访问令牌
        const result = await getAnonymousAccessToken();
        return new Response(JSON.stringify(result, null, 2), { headers: corsHeaders });

      } else if (url.pathname === '/create') {
        // 仅创建匿名用户
        try {
          const userData = await createAnonymousUser();
          return new Response(JSON.stringify({
            success: true,
            data: userData,
            timestamp: new Date().toISOString()
          }, null, 2), { headers: corsHeaders });
        } catch (error) {
          return new Response(JSON.stringify({
            success: false,
            error: error.message,
            timestamp: new Date().toISOString()
          }, null, 2), { headers: corsHeaders });
        }

      } else if (url.pathname === '/health') {
        // 健康检查
        return new Response(JSON.stringify({
          status: 'healthy',
          service: 'warp-anonymous-token-service',
          timestamp: new Date().toISOString(),
          worker_location: request.cf?.colo || 'unknown'
        }, null, 2), { headers: corsHeaders });

      } else {
        // 404
        return new Response(JSON.stringify({
          error: 'Not Found',
          message: 'Available endpoints: /, /token, /create, /health'
        }, null, 2), {
          status: 404,
          headers: corsHeaders
        });
      }

    } catch (error) {
      console.error('Worker error:', error);
      return new Response(JSON.stringify({
        success: false,
        error: 'Internal Server Error',
        message: error.message,
        timestamp: new Date().toISOString()
      }, null, 2), {
        status: 500,
        headers: corsHeaders
      });
    }
  }
};
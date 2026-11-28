/* 数据接口封装
 *
 * 页面数据全部来自 GET /api/*（见 backend/routes.py）。
 * 开发时由 vite.config.js 的 proxy 转发到 8077；构建后与后端同源，直接请求。
 */

const API_BASE = import.meta.env.VITE_API_BASE || ''

class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function getJson(path) {
  let res
  try {
    res = await fetch(API_BASE + path, {
      headers: { Accept: 'application/json' },
      // 交给浏览器带 If-None-Match 做协商缓存；后端回 304 时走本地缓存
      cache: 'no-cache',
    })
  } catch (e) {
    throw new ApiError('网络请求失败，请确认后端已启动', 0)
  }

  if (!res.ok) {
    // 后端统一返回 { error: { code, message } }
    let detail = ''
    try {
      const body = await res.json()
      detail = body?.error?.message || ''
    } catch (e) {
      /* 响应不是 JSON，用状态码兜底 */
    }
    throw new ApiError(detail || `接口返回 ${res.status}`, res.status)
  }
  return res.json()
}

/* ------------------------------------------------------------ 数据接口 */

export const loadMeta = () => getJson('/api/meta')

export const loadIndex = () => getJson('/api/index')

export const loadPatch = (patchId) => getJson('/api/patch/' + encodeURIComponent(patchId))

export const loadNlpTopics = () => getJson('/api/nlp/topics')

export const loadNlpItems = () => getJson('/api/nlp/items')

/* 已复核引文：传 patchId 只取该版本（约 31 KB），不传则全量（约 400 KB）。
   这份数据缺失不该让整页失败，调用方拿到空数组即可。 */
export const loadVerified = async (patchId) => {
  const path = patchId
    ? '/api/verified?patch_id=' + encodeURIComponent(patchId)
    : '/api/verified'
  try {
    return await getJson(path)
  } catch (e) {
    return []
  }
}

/* ------------------------------------------------------------ 问答接口 */

export const askAgent = async (patchId, question) => {
  let res
  try {
    res = await fetch(API_BASE + '/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ patch_id: patchId, question }),
    })
  } catch (e) {
    throw new ApiError('问答接口连接失败', 0)
  }
  if (!res.ok) throw new ApiError('接口返回 ' + res.status, res.status)
  return res.json()
}

export { ApiError }

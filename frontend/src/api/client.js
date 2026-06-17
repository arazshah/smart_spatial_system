const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const text = await response.text();

  let data = null;

  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }

  if (!response.ok) {
    const message =
      data?.detail ||
      data?.message ||
      `Request failed with status ${response.status}`;

    throw new Error(
      typeof message === "string" ? message : JSON.stringify(message)
    );
  }

  return data;
}

async function uploadFile(path, file) {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    body: formData,
  });

  const text = await response.text();

  let data = null;

  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }

  if (!response.ok) {
    const message =
      data?.detail ||
      data?.message ||
      `Upload failed with status ${response.status}`;

    throw new Error(
      typeof message === "string" ? message : JSON.stringify(message)
    );
  }

  return data;
}

export function getHealth() {
  return request("/health");
}

export function runQuery(payload) {
  return request("/query", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function submitFeedback(payload) {
  return request("/feedback", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listRequests() {
  return request("/requests");
}

export function getRequest(requestId) {
  return request(`/requests/${encodeURIComponent(requestId)}`);
}

export function getMapLayers(requestId) {
  return request(`/requests/${encodeURIComponent(requestId)}/map-layers`);
}

export function getWeights() {
  return request("/weights");
}

export function saveWeights() {
  return request("/weights/save", {
    method: "POST",
  });
}

export function reloadWeights() {
  return request("/weights/reload", {
    method: "POST",
  });
}

export function getOutputManifest(requestId) {
  return request(`/requests/${encodeURIComponent(requestId)}/outputs`);
}

export function listOutputFiles(requestId) {
  return request(`/requests/${encodeURIComponent(requestId)}/outputs/files`);
}

export function outputFileUrl(requestId, filename) {
  return `${API_BASE_URL}/requests/${encodeURIComponent(
    requestId
  )}/outputs/files/${encodeURIComponent(filename)}`;
}

export function uploadRaster(file) {
  return uploadFile("/uploads/raster", file);
}

export function uploadVector(file) {
  return uploadFile("/uploads/vector", file);
}

export function listUploads() {
  return request("/uploads");
}

export function uploadFileByKind(kind, file) {
  if (kind === "raster") {
    return uploadRaster(file);
  }

  if (kind === "vector") {
    return uploadVector(file);
  }

  throw new Error(`Unsupported upload kind: ${kind}`);
}

export { API_BASE_URL };

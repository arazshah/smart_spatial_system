const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

async function parseResponse(response) {
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

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };

  if (!(options.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  });

  return parseResponse(response);
}

async function uploadFile(path, file, extraFields = {}) {
  const formData = new FormData();
  formData.append("file", file);

  Object.entries(extraFields).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      formData.append(key, String(value));
    }
  });

  return request(path, {
    method: "POST",
    body: formData,
  });
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

export function uploadRaster(file, projectId) {
  return uploadFile("/uploads/raster", file, { project_id: projectId });
}

export function uploadVector(file, projectId) {
  return uploadFile("/uploads/vector", file, { project_id: projectId });
}

export function uploadFileByKind(kind, file, projectId) {
  if (kind === "raster") {
    return uploadRaster(file, projectId);
  }

  if (kind === "vector") {
    return uploadVector(file, projectId);
  }

  throw new Error(`Unsupported upload kind: ${kind}`);
}

export function listUploads() {
  return request("/uploads");
}

export function createProject(payload) {
  return request("/projects", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listProjects() {
  return request("/projects");
}

export function getProject(projectId) {
  return request(`/projects/${encodeURIComponent(projectId)}`);
}

export { API_BASE_URL };

const SUCCESS_KEYS = new Set(["ok", "data", "meta"]);
const ERROR_KEYS = new Set(["ok", "error"]);
const ERROR_PAYLOAD_KEYS = new Set(["code", "message", "details"]);

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasOnlyKeys(source, allowedKeys) {
  return Object.keys(source).every((key) => allowedKeys.has(key));
}

function createTransportContractError(message, { status = 0, payload = null, contentType = "" } = {}) {
  const error = new Error(message);
  error.name = "TransportContractError";
  error.code = "transport_contract_violation";
  error.status = status;
  error.payload = payload;
  error.content_type = contentType;
  return error;
}

function assertSuccessEnvelope(payload, path, status, contentType) {
  const hasData = Object.prototype.hasOwnProperty.call(payload, "data");
  const hasMeta = Object.prototype.hasOwnProperty.call(payload, "meta");
  if (
    !isPlainObject(payload)
    || payload.ok !== true
    || !hasData
    || Object.prototype.hasOwnProperty.call(payload, "error")
    || !hasOnlyKeys(payload, SUCCESS_KEYS)
    || (hasMeta && !isPlainObject(payload.meta))
  ) {
    throw createTransportContractError(
      `${path} returned invalid success transport envelope`,
      { status, payload, contentType },
    );
  }
  return payload.data;
}

function assertErrorEnvelope(payload, path, status, contentType) {
  if (
    !isPlainObject(payload)
    || payload.ok !== false
    || Object.prototype.hasOwnProperty.call(payload, "data")
    || !isPlainObject(payload.error)
    || !hasOnlyKeys(payload, ERROR_KEYS)
  ) {
    throw createTransportContractError(
      `${path} returned invalid error transport envelope`,
      { status, payload, contentType },
    );
  }

  const errorPayload = payload.error;
  const code = String(errorPayload.code || "").trim();
  const message = String(errorPayload.message || "").trim();
  const hasDetails = Object.prototype.hasOwnProperty.call(errorPayload, "details");
  if (
    !code
    || !message
    || !hasOnlyKeys(errorPayload, ERROR_PAYLOAD_KEYS)
    || (hasDetails && !isPlainObject(errorPayload.details))
  ) {
    throw createTransportContractError(
      `${path} returned invalid error transport envelope`,
      { status, payload, contentType },
    );
  }

  return {
    code,
    message,
    details: hasDetails ? errorPayload.details : undefined,
  };
}

async function readJsonPayload(response, path, contentType) {
  try {
    return await response.json();
  } catch {
    throw createTransportContractError(
      `${path} returned invalid JSON transport response`,
      { status: response.status, contentType },
    );
  }
}

export async function readTransportData(response, path) {
  const contentType = String(response.headers?.get?.("content-type") || "").trim();
  const normalizedContentType = contentType.toLowerCase();
  if (!normalizedContentType.includes("application/json")) {
    throw createTransportContractError(
      `${path} returned non-JSON transport response`,
      { status: response.status, contentType },
    );
  }

  const payload = await readJsonPayload(response, path, contentType);
  if (response.ok) {
    return assertSuccessEnvelope(payload, path, response.status, contentType);
  }

  const errorPayload = assertErrorEnvelope(payload, path, response.status, contentType);
  const error = new Error(errorPayload.message);
  error.name = "ApiError";
  error.code = errorPayload.code;
  error.status = response.status;
  error.payload = payload;
  if (errorPayload.details !== undefined) {
    error.details = errorPayload.details;
  }
  throw error;
}
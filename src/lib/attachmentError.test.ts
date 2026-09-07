import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { classifyAttachmentUploadFailure } from "./attachmentError";

function apiError(errorCode: string, message = "ignored"): ApiError {
  return new ApiError(message, 400, { errorCode, retryable: false, correlationId: "c1" });
}

describe("classifyAttachmentUploadFailure — POST-5.1 B3 safe error mapping", () => {
  it("maps payload_too_large (413) to 'Image is too large.'", () => {
    expect(classifyAttachmentUploadFailure(apiError("payload_too_large"))).toBe("Image is too large.");
  });

  it("maps unsupported_media_type (415) to \"This image format isn't supported.\"", () => {
    expect(classifyAttachmentUploadFailure(apiError("unsupported_media_type"))).toBe(
      "This image format isn't supported.",
    );
  });

  it("maps not_found (404) to 'Unable to upload this image in this conversation.'", () => {
    expect(classifyAttachmentUploadFailure(apiError("not_found"))).toBe(
      "Unable to upload this image in this conversation.",
    );
  });

  it("maps connector_unavailable (503) to 'Image upload is temporarily unavailable.'", () => {
    expect(classifyAttachmentUploadFailure(apiError("connector_unavailable"))).toBe(
      "Image upload is temporarily unavailable.",
    );
  });

  it("falls back to the backend's own userMessage for an unrecognized errorCode", () => {
    expect(classifyAttachmentUploadFailure(apiError("some_other_code", "A specific safe message."))).toBe(
      "A specific safe message.",
    );
  });

  it("falls back to the generic 'Upload failed. Try again.' for a non-ApiError (network failure)", () => {
    expect(classifyAttachmentUploadFailure(new TypeError("Failed to fetch"))).toBe("Upload failed. Try again.");
  });

  it("never echoes a raw exception message for a non-ApiError", () => {
    const result = classifyAttachmentUploadFailure(new Error("gs://real-bucket/real-object-key leaked"));
    expect(result).not.toContain("gs://");
    expect(result).toBe("Upload failed. Try again.");
  });
});

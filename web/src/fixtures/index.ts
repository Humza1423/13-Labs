import type { PreviewFixture } from "../contract";
import raw from "./preview-run.json";

// JSON imports widen string literals; the Python test validates this file
// against the Pydantic mirror, so the cast is checked in CI.
export const previewFixture = raw as unknown as PreviewFixture;

/**
 * Sajilo Pasal – Client-side image compression
 *
 * Uses Compressor.js to resize/compress images in the browser before
 * the form is submitted. This prevents a heavy phone-camera photo
 * (3-5MB) from blocking Django's web worker thread during the upload
 * over a slow Ncell/NTC mobile connection.
 *
 * The server-side 5MB size validator and Pillow content-sniff still
 * run after upload completes — this is a UX performance improvement,
 * not a replacement for server-side validation.
 *
 * Usage: call initImageCompressor(inputId, previewId, statusId)
 * where:
 *   inputId   — id of the <input type="file"> element
 *   previewId — id of an <img> element for preview (optional, pass null to skip)
 *   statusId  — id of a <span> for status text (optional, pass null to skip)
 */

"use strict";

function initImageCompressor(inputId, previewId, statusId) {
  const input  = document.getElementById(inputId);
  const status = statusId  ? document.getElementById(statusId)  : null;
  const preview = previewId ? document.getElementById(previewId) : null;

  if (!input) return;

  input.addEventListener("change", function (e) {
    const file = e.target.files[0];
    if (!file) return;

    // Only compress image types we actually accept
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      // Let the server-side validator handle rejection of unsupported types —
      // don't try to compress a TIFF or PDF, just submit as-is and let Django
      // surface the error.
      return;
    }

    if (status) {
      status.textContent = "Compressing image…";
      status.style.display = "inline";
    }

    // Compressor.js options — balance quality vs file size for a
    // restaurant/shop menu image context: legible, not pristine.
    new Compressor(file, {
      quality: 0.75,         // 75% JPEG quality — good enough for menu images
      maxWidth: 1200,        // cap width at 1200px; most menu cards are small
      maxHeight: 1200,
      mimeType: "image/jpeg",// normalize all output to JPEG for consistency

      success(compressedBlob) {
        // Swap the file input's file for the compressed blob using
        // DataTransfer, which is the only way to programmatically set
        // a file input's value cross-browser without a real user gesture.
        const compressedFile = new File(
          [compressedBlob],
          file.name.replace(/\.[^.]+$/, ".jpg"),  // rename to .jpg
          { type: "image/jpeg", lastModified: Date.now() }
        );

        const dt = new DataTransfer();
        dt.items.add(compressedFile);
        input.files = dt.files;

        if (status) {
          const originalKB  = Math.round(file.size / 1024);
          const compressedKB = Math.round(compressedBlob.size / 1024);
          status.textContent = `Compressed: ${originalKB}KB → ${compressedKB}KB`;
        }

        if (preview) {
          const reader = new FileReader();
          reader.onload = (ev) => { preview.src = ev.target.result; };
          reader.readAsDataURL(compressedFile);
        }
      },

      error(err) {
        // If compression fails for any reason, let the original file
        // through — the server-side 5MB limit will catch it if it's too
        // large, and the user gets a clear error rather than a silent
        // failure or a broken form.
        console.warn("Compressor.js error (original file used instead):", err.message);
        if (status) {
          status.textContent = "Compression skipped — original file will be uploaded.";
        }
      },
    });
  });
}


#pragma once

// Reduces model output to something worth reading aloud.
// Returns a malloc'd string the caller frees, or NULL if nothing remains.
char *speakable(const char *input);

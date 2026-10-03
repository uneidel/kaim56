// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"encoding/base64"
	"io"
	"os"
	"sort"
)

func encodeB64(b []byte) string { return base64.StdEncoding.EncodeToString(b) }

func num(v any) float64 {
	if f, ok := v.(float64); ok {
		return f
	}
	return 0
}

// sortByNum: newest (largest) first.
func sortByNum(rows []map[string]any, key string) {
	sort.SliceStable(rows, func(a, b int) bool { return num(rows[a][key]) > num(rows[b][key]) })
}

func readAllStdin() ([]byte, error) { return io.ReadAll(os.Stdin) }

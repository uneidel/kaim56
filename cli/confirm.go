// kAIm56 — self-hosted Firecracker AI-agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"bufio"
	"fmt"
	"os"
	"strings"

	"golang.org/x/term"
)

// confirm: the user types `want` to go ahead (for deletes).
func confirm(q, want string) bool {
	fmt.Fprintf(os.Stderr, "%s: ", q)
	s, _ := bufio.NewReader(os.Stdin).ReadString('\n')
	return strings.TrimSpace(s) == want
}

// readSecret: a value typed without echo.
func readSecret(what string) (string, error) {
	fmt.Fprintf(os.Stderr, "%s (hidden): ", what)
	b, err := term.ReadPassword(int(os.Stdin.Fd()))
	fmt.Fprintln(os.Stderr)
	if err != nil {
		s, err2 := bufio.NewReader(os.Stdin).ReadString('\n')
		if err2 != nil && s == "" {
			return "", err
		}
		return strings.TrimSpace(s), nil
	}
	return strings.TrimSpace(string(b)), nil
}

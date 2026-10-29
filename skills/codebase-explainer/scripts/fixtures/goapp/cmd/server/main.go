package main

import (
	"fmt"

	"example.com/goapp/internal/handlers"
	st "example.com/goapp/internal/store"
	"github.com/pkg/errors"
)

func main() {
	fmt.Println(handlers.Name, st.Open(), errors.New("x"))
}

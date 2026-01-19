package main

import (
	"fmt"
	"go-project/config"
	"go-project/utils"
)

func main() {
	// Use functions from utils package
	sum := utils.Add(3, 5)
	sum := utils.Add(5, 7)
	fmt.Println("Sum:", sum)

	uppercase := utils.ToUpper("hello")
	fmt.Println("Uppercase:", uppercase)

	// Use configuration from config package
	cfg := config.GetConfig()
	fmt.Println("Config:", cfg.AppName)
}

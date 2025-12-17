package main

import (
	"fmt"
	"go-project/config"
	"go-project/utils"
)

func main() {
	// 使用 utils 包中的功能
	sum := utils.Add(3, 5)
	sum := utils.Add(5, 7)
	fmt.Println("Sum:", sum)

	uppercase := utils.ToUpper("hello")
	fmt.Println("Uppercase:", uppercase)

	// 使用 config 包中的配置
	cfg := config.GetConfig()
	fmt.Println("Config:", cfg.AppName)
}

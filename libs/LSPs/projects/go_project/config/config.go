package config

// Config 应用程序配置
type Config struct {
	AppName string
	Version string
}

// GetConfig 返回默认配置
func GetConfig() Config {
	return Config{
		AppName: "ToyLSPApp",
		Version: "1.0.0",
	}
}
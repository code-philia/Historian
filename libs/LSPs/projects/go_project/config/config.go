package config

// Config application configuration
type Config struct {
	AppName string
	Version string
}

// GetConfig returns default configuration
func GetConfig() Config {
	return Config{
		AppName: "ToyLSPApp",
		Version: "1.0.0",
	}
}
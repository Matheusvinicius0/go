package main

import (
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

// Estrutura para armazenar o cache em memória
type CacheItem struct {
	Data       []byte
	Headers    http.Header
	Expiration time.Time
}

var cache sync.Map
var cacheDuration = 24 * time.Hour

// Mapa para armazenar as rotas dos addons
var meusAddons = make(map[string]string)

// Função que lê a variável de ambiente ADDONS_LIST e preenche o mapa
func carregarAddons() {
	addonsEnv := os.Getenv("ADDONS_LIST")
	if addonsEnv == "" {
		fmt.Println("Aviso: Variável 'ADDONS_LIST' não encontrada.")
		return
	}

	// Separa por vírgulas para permitir múltiplos addons
	pares := strings.Split(addonsEnv, ",")
	for _, par := range pares {
		kv := strings.SplitN(par, "=", 2)
		if len(kv) == 2 {
			rota := strings.TrimSpace(kv[0])
			link := strings.TrimSpace(kv[1])
			meusAddons[rota] = link
			fmt.Printf("Addon carregado: %s -> %s\n", rota, link)
		}
	}
}

func proxyHandler(w http.ResponseWriter, r *http.Request) {
	var targetBaseURL string
	var pathRestante string

	// Verifica qual rota o usuário está acessando
	for prefixo, urlOriginal := range meusAddons {
		if strings.HasPrefix(r.URL.Path, prefixo) {
			targetBaseURL = urlOriginal
			pathRestante = strings.TrimPrefix(r.URL.Path, prefixo)
			break
		}
	}

	if targetBaseURL == "" {
		http.Error(w, "Addon não encontrado neste servidor", http.StatusNotFound)
		return
	}

	// Monta a URL para o Hugging Face
	targetURL := targetBaseURL + pathRestante
	if r.URL.RawQuery != "" {
		targetURL += "?" + r.URL.RawQuery
	}

	// 1. Verifica se está no cache
	if item, found := cache.Load(targetURL); found {
		cItem := item.(CacheItem)
		if time.Now().Before(cItem.Expiration) {
			for k, v := range cItem.Headers {
				w.Header()[k] = v
			}
			w.Header().Set("X-Cache", "HIT")
			w.Write(cItem.Data)
			return
		}
		cache.Delete(targetURL)
	}

	// 2. Se não estiver, busca do servidor original (Hugging Face)
	resp, err := http.Get(targetURL)
	if err != nil {
		http.Error(w, "Erro ao contatar o servidor original", http.StatusInternalServerError)
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		http.Error(w, "Erro ao ler a resposta", http.StatusInternalServerError)
		return
	}

	// 3. Salva no cache
	cache.Store(targetURL, CacheItem{
		Data:       body,
		Headers:    resp.Header.Clone(),
		Expiration: time.Now().Add(cacheDuration),
	})

	// 4. Retorna a resposta ao usuário
	for k, v := range resp.Header {
		w.Header()[k] = v
	}
	w.Header().Set("X-Cache", "MISS")
	w.Write(body)
}

func main() {
	carregarAddons()

	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	http.HandleFunc("/", proxyHandler)
	fmt.Printf("Servidor Proxy rodando na porta %s\n", port)
	
	err := http.ListenAndServe(":"+port, nil)
	if err != nil {
		fmt.Println("Erro fatal no servidor:", err)
	}
}

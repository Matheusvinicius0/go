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

type CacheItem struct {
	Data       []byte
	Headers    http.Header
	Expiration time.Time
}

var cache sync.Map
var cacheDuration = 24 * time.Hour
var meusAddons = make(map[string]string)

func carregarAddons() {
	addonsEnv := os.Getenv("ADDONS_LIST")
	if addonsEnv == "" {
		fmt.Println("Aviso: Variável 'ADDONS_LIST' não encontrada.")
		return
	}

	pares := strings.Split(addonsEnv, ",")
	for _, par := range pares {
		kv := strings.SplitN(par, "=", 2)
		if len(kv) == 2 {
			// Remove espaços e garante que não tenha barra no prefixo
			rota := strings.Trim(strings.TrimSpace(kv[0]), "/")
			link := strings.TrimSpace(kv[1])
			meusAddons[rota] = link
			fmt.Printf("Addon carregado: %s -> %s\n", rota, link)
		}
	}
}

func proxyHandler(w http.ResponseWriter, r *http.Request) {
	// Remove a barra inicial do caminho para comparar com as rotas
	caminhoLimpo := strings.TrimPrefix(r.URL.Path, "/")
	
	var targetBaseURL string
	var pathRestante string

	for prefixo, urlOriginal := range meusAddons {
		if strings.HasPrefix(caminhoLimpo, prefixo) {
			targetBaseURL = urlOriginal
			// Pega o resto da URL após o prefixo (ex: /manifest.json)
			pathRestante = strings.TrimPrefix(caminhoLimpo, prefixo)
			break
		}
	}

	if targetBaseURL == "" {
		http.Error(w, "Addon não encontrado neste servidor", http.StatusNotFound)
		return
	}

	// Monta a URL final garantindo a estrutura correta
	targetURL := strings.TrimRight(targetBaseURL, "/") + "/" + strings.TrimLeft(pathRestante, "/")
	if r.URL.RawQuery != "" {
		targetURL += "?" + r.URL.RawQuery
	}

	// 1. Verifica no Cache
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

	// 2. Busca no Hugging Face
	resp, err := http.Get(targetURL)
	if err != nil {
		http.Error(w, "Erro ao conectar com servidor original", http.StatusInternalServerError)
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		http.Error(w, "Erro ao ler dados", http.StatusInternalServerError)
		return
	}

	// 3. Salva no Cache
	cache.Store(targetURL, CacheItem{
		Data:       body,
		Headers:    resp.Header.Clone(),
		Expiration: time.Now().Add(cacheDuration),
	})

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
	fmt.Printf("Proxy rodando na porta %s\n", port)
	
	err := http.ListenAndServe(":"+port, nil)
	if err != nil {
		fmt.Println("Erro fatal:", err)
	}
}

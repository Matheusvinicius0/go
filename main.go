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
	// Isso aparece nos Logs do Render, facilitando o seu debug
	fmt.Printf("DEBUG: Valor lido da variável ADDONS_LIST: '%s'\n", addonsEnv)
	
	if addonsEnv == "" {
		fmt.Println("ERRO CRÍTICO: Variável 'ADDONS_LIST' não encontrada no ambiente!")
		return
	}

	// Suporta múltiplos addons separados por vírgula
	pares := strings.Split(addonsEnv, ",")
	for _, par := range pares {
		kv := strings.SplitN(par, "=", 2)
		if len(kv) == 2 {
			rota := strings.Trim(strings.TrimSpace(kv[0]), "/")
			link := strings.TrimRight(strings.TrimSpace(kv[1]), "/")
			meusAddons[rota] = link
			fmt.Printf("CONFIGURADO: Rota '/%s' aponta para '%s'\n", rota, link)
		}
	}
}

func proxyHandler(w http.ResponseWriter, r *http.Request) {
	// Pega o caminho, remove a barra inicial e pega a primeira parte (ex: fenixflix)
	partes := strings.Split(strings.TrimPrefix(r.URL.Path, "/"), "/")
	prefixo := partes[0]
	
	targetBaseURL, ok := meusAddons[prefixo]
	if !ok {
		http.Error(w, "Addon não mapeado nesta rota", http.StatusNotFound)
		return
	}

	// Monta o resto da URL (o que vem depois do prefixo)
	pathRestante := strings.TrimPrefix(r.URL.Path, "/"+prefixo)
	targetURL := targetBaseURL + pathRestante
	if r.URL.RawQuery != "" {
		targetURL += "?" + r.URL.RawQuery
	}

	// Verificação de Cache
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
	}

	// Requisição ao servidor real
	resp, err := http.Get(targetURL)
	if err != nil {
		http.Error(w, "Erro ao conectar ao servidor original", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		http.Error(w, "Erro ao ler resposta", http.StatusInternalServerError)
		return
	}

	// Salva no Cache
	cache.Store(targetURL, CacheItem{Data: body, Headers: resp.Header.Clone(), Expiration: time.Now().Add(cacheDuration)})

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
	fmt.Printf("Servidor proxy iniciado na porta %s...\n", port)
	http.ListenAndServe(":"+port, nil)
}

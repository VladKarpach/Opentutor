"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { LogIn } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export default function LoginPage() {
  const [email, setEmail] = useState("admin123");
  const [password, setPassword] = useState("1234");
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password) {
      toast.error("Заполните все поля");
      return;
    }

    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Неверный email или пароль");
      }
      const data = await res.json();
      localStorage.setItem("token", data.access_token);
      toast.success("Добро пожаловать, " + email);
      router.push("/");
      router.refresh();
    } catch (err: any) {
      toast.error(err.message || "Неверный email или пароль");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background">
      <div className="w-full max-w-md space-y-8 p-4">
        <div className="mx-auto">
          <div className="flex justify-center">
            <LogIn className="h-10 w-10 text-brand" />
          </div>
          <h2 className="text-2xl font-bold text-center text-foreground">
            Вход в OpenTutor
          </h2>
          <p className="text-center text-muted-foreground">
            Войдите, чтобы начать обучение
          </p>
        </div>

        <div className="mt-8 space-y-4">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <label htmlFor="email" className="block text-sm font-medium text-foreground mb-1">
                Email
              </label>
              <Input
                id="email"
                type="text"
                placeholder="admin123"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <label htmlFor="password" className="block text-sm font-medium text-foreground mb-1">
                Пароль
              </label>
              <Input
                id="password"
                type="password"
                placeholder="1234"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            <Button
              type="submit"
              disabled={loading}
              className="w-full"
            >
              {loading ? "Вход..." : "Войти"}
            </Button>
          </form>

          <div className="text-center text-sm text-muted-foreground">
            Тестовый аккаунт: admin123 / 1234
          </div>
        </div>
      </div>
    </div>
  );
}

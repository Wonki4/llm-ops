import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function LoginPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl font-bold">LiteLLM Portal</CardTitle>
          <CardDescription>사번으로 로그인하세요</CardDescription>
        </CardHeader>
        <CardContent>
          <a href="/api/proxy/auth/login?return_to=/teams">
            <Button className="w-full" size="lg">
              SSO 로그인
            </Button>
          </a>
        </CardContent>
      </Card>
    </div>
  );
}

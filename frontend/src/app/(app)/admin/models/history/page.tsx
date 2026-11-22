import { Suspense } from "react";
import { AdminPage } from "@/components/admin-page";
import { ModelLifecycle } from "@/components/model-lifecycle";
export default function Page() {return <AdminPage><Suspense><ModelLifecycle /></Suspense></AdminPage>;}

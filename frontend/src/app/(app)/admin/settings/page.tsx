"use client";
import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { legacySettingsLocation } from "@/lib/legacy-navigation";
function Redirect(){const router=useRouter();const q=useSearchParams();useEffect(()=>{router.replace(legacySettingsLocation(q.toString(),window.location.hash));},[router,q]);return <p aria-busy="true">…</p>;}
export default function Page(){return <Suspense><Redirect /></Suspense>;}

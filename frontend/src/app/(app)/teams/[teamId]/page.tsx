"use client";
import { Suspense } from "react";
import TeamDetail from "@/components/team-detail";
export default function Page({params}: {params:Promise<{teamId:string}>}) {return <Suspense><TeamDetail params={params} /></Suspense>;}

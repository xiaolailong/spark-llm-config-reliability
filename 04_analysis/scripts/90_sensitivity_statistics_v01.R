#!/usr/bin/env Rscript

# 90_sensitivity_statistics_v01.R
#
# Sensitivity-only Initial analysis.
# Uses the SAME statistical design as Primary Statistics V1:
# Wilson CI, paired transitions, exact McNemar+Holm,
# 10,000 A-F stratified task bootstrap, and
# sensitivity_success ~ model * condition + category + (1 | task_id)
# with the same diagnostic gate and GEE fallback.
#
# No repair/final analysis is performed here.

options(stringsAsFactors = FALSE, warn = 1)

get_script_path <- function() {
  args <- commandArgs(trailingOnly = FALSE)
  hit <- grep("^--file=", args, value = TRUE)
  if (length(hit) > 0) {
    return(normalizePath(sub("^--file=", "", hit[1]), winslash="/", mustWork=TRUE))
  }
  frames <- sys.frames()
  for (i in rev(seq_along(frames))) {
    of <- frames[[i]]$ofile
    if (!is.null(of)) return(normalizePath(of,winslash="/",mustWork=TRUE))
  }
  stop("Cannot determine script path.")
}

script_path <- get_script_path()
script_dir <- dirname(script_path)
project_root <- normalizePath(file.path(script_dir,"..","..",".."),winslash="/",mustWork=TRUE)
input_path <- file.path(project_root,"paper_core","04_analysis","sensitivity_v01",
                        "rescored_v01","sensitivity_master_540_v01.csv")
out_dir <- file.path(project_root,"paper_core","04_analysis","sensitivity_v01",
                     "statistics_v01")
dir.create(out_dir,recursive=TRUE,showWarnings=FALSE)

log_path <- file.path(out_dir,"sensitivity_statistics_console_v01.txt")
zz <- file(log_path,open="wt",encoding="UTF-8")
sink(zz,type="output",split=TRUE)
sink(zz,type="message",append=TRUE)
on.exit({
  try(sink(type="message"),silent=TRUE)
  try(sink(type="output"),silent=TRUE)
  try(close(zz),silent=TRUE)
},add=TRUE)

needed <- c("lme4","emmeans")
missing <- needed[!vapply(needed,requireNamespace,logical(1),quietly=TRUE)]
if (length(missing)>0) stop("Missing R packages: ",paste(missing,collapse=", "))

sha256_file <- function(path) {
  ans <- suppressWarnings(system2("sha256sum",shQuote(path),stdout=TRUE,stderr=TRUE))
  if (length(ans)<1 || !grepl("^[0-9a-fA-F]{64}",ans[1])) return(NA_character_)
  sub("\\s+.*$","",ans[1])
}

boolify <- function(x) {
  if (is.logical(x)) return(x)
  y <- tolower(trimws(as.character(x)))
  z <- rep(NA,length(y))
  z[y %in% c("true","1","yes","y","pass","success","succeeded")] <- TRUE
  z[y %in% c("false","0","no","n","fail","failure","failed")] <- FALSE
  z
}

wilson_ci <- function(x,n,conf=.95) {
  if (n<=0) return(c(NA_real_,NA_real_))
  z <- qnorm(1-(1-conf)/2)
  phat <- x/n
  den <- 1+z^2/n
  center <- (phat+z^2/(2*n))/den
  half <- z*sqrt(phat*(1-phat)/n + z^2/(4*n^2))/den
  c(max(0,center-half),min(1,center+half))
}

prop_row <- function(model,condition,x,n,type) {
  ci <- wilson_ci(x,n)
  data.frame(
    analysis=type,model=model,condition=condition,success=x,n=n,
    proportion=x/n,percent=100*x/n,
    ci_low=ci[1],ci_high=ci[2],
    ci_low_percent=100*ci[1],ci_high_percent=100*ci[2],
    stringsAsFactors=FALSE
  )
}

dat <- read.csv(input_path,check.names=FALSE,na.strings=c("","NA"),
                stringsAsFactors=FALSE,fileEncoding="UTF-8-BOM")

model_map <- c(qwen="Qwen",deepseek="DeepSeek",gpt6_sol="GPT")
condition_map <- c(
  C0_closed_book="C0",
  C1_frozen_official_docs_v04="C1",
  C0="C0",C1="C1"
)
category_map <- c(
  A_resource="A",B_driver_executor="B",C_yarn_deploy="C",
  D_source_precedence="D",E_dependency_interaction="E",F_integrated="F",
  A="A",B="B",C="C",D="D",E="E",F="F"
)

dat$model_short <- unname(model_map[dat$model])
dat$condition_short <- unname(condition_map[dat$condition])
dat$category_short <- unname(category_map[dat$category])
dat$strict_b <- boolify(dat$strict_initial_success)
dat$sens_b <- boolify(dat$sensitivity_initial_success)
dat$flip_b <- boolify(dat$sensitivity_flip_0_to_1)

stopifnot(
  nrow(dat)==540,
  length(unique(dat$task_id))==90,
  !anyDuplicated(dat[,c("model_short","condition_short","task_id")]),
  all(!is.na(dat$model_short)),
  all(!is.na(dat$condition_short)),
  all(!is.na(dat$category_short)),
  all(!is.na(dat$strict_b)),
  all(!is.na(dat$sens_b)),
  all(!is.na(dat$flip_b)),
  all(!dat$strict_b | dat$sens_b),
  sum(dat$flip_b)==55
)
if (!all(table(dat$task_id)==6)) stop("Each task must have six fully crossed rows.")

# Verify original strict Primary counts have not changed.
strict_expected <- data.frame(
  model=c("Qwen","Qwen","DeepSeek","DeepSeek","GPT","GPT"),
  condition=rep(c("C0","C1"),3),
  success=c(23,61,65,79,70,68)
)
for (i in seq_len(nrow(strict_expected))) {
  z <- subset(dat,model_short==strict_expected$model[i] &
                  condition_short==strict_expected$condition[i])
  if (sum(z$strict_b)!=strict_expected$success[i] || nrow(z)!=90) {
    stop("Strict Primary count mismatch.")
  }
}

mods <- c("Qwen","DeepSeek","GPT")
conds <- c("C0","C1")

# 1) Sensitivity Wilson
sens_rows <- list(); k <- 1
for (m in mods) for (cnd in conds) {
  z <- subset(dat,model_short==m & condition_short==cnd)
  sens_rows[[k]] <- prop_row(m,cnd,sum(z$sens_b),nrow(z),"Sensitivity Initial")
  k <- k+1
}
sens_wilson <- do.call(rbind,sens_rows)
write.csv(sens_wilson,file.path(out_dir,"01_sensitivity_wilson_v01.csv"),
          row.names=FALSE,fileEncoding="UTF-8")

# 2) Strict vs sensitivity gains by cell
cmp <- data.frame()
for (m in mods) for (cnd in conds) {
  z <- subset(dat,model_short==m & condition_short==cnd)
  cmp <- rbind(cmp,data.frame(
    model=m,condition=cnd,n=nrow(z),
    strict_success=sum(z$strict_b),
    sensitivity_success=sum(z$sens_b),
    flips=sum(z$flip_b),
    strict_percent=100*mean(z$strict_b),
    sensitivity_percent=100*mean(z$sens_b),
    gain_pp=100*(mean(z$sens_b)-mean(z$strict_b)),
    stringsAsFactors=FALSE
  ))
}
write.csv(cmp,file.path(out_dir,"02_strict_vs_sensitivity_by_cell_v01.csv"),
          row.names=FALSE,fileEncoding="UTF-8")

# 3) Paired sensitivity transitions + exact McNemar + Holm
paired <- list(); k <- 1
for (m in mods) {
  z <- subset(dat,model_short==m,select=c("task_id","condition_short","sens_b"))
  w <- reshape(z,idvar="task_id",timevar="condition_short",direction="wide")
  names(w) <- sub("^sens_b\\.","",names(w))
  pp <- sum(w$C0 & w$C1)
  fp <- sum(!w$C0 & w$C1)
  pf <- sum(w$C0 & !w$C1)
  ff <- sum(!w$C0 & !w$C1)
  p_exact <- if ((fp+pf)>0) binom.test(fp,fp+pf,p=.5,alternative="two.sided")$p.value else 1
  paired[[k]] <- data.frame(
    model=m,pass_to_pass=pp,fail_to_pass=fp,pass_to_fail=pf,fail_to_fail=ff,
    discordant=fp+pf,c0_success=pp+pf,c1_success=pp+fp,
    risk_difference=(fp-pf)/90,risk_difference_pp=100*(fp-pf)/90,
    exact_mcnemar_p=p_exact,stringsAsFactors=FALSE
  )
  k <- k+1
}
paired_df <- do.call(rbind,paired)
paired_df$holm_p <- p.adjust(paired_df$exact_mcnemar_p,method="holm")
write.csv(paired_df,file.path(out_dir,"03_sensitivity_paired_mcnemar_v01.csv"),
          row.names=FALSE,fileEncoding="UTF-8")

# 4) Same A-F stratified task-level bootstrap
set.seed(20261001)
B <- 10000L
task_meta <- unique(dat[,c("task_id","category_short")])
task_meta <- task_meta[order(task_meta$category_short,task_meta$task_id),]
n_tasks <- nrow(task_meta)
arr <- array(NA_real_,dim=c(n_tasks,length(mods),length(conds)),
             dimnames=list(task_meta$task_id,mods,conds))
for (i in seq_len(n_tasks)) {
  tid <- task_meta$task_id[i]
  for (j in seq_along(mods)) for (q in seq_along(conds)) {
    zz <- subset(dat,task_id==tid & model_short==mods[j] &
                     condition_short==conds[q])
    if (nrow(zz)!=1) stop("Bootstrap matrix construction failed.")
    arr[i,j,q] <- as.numeric(zz$sens_b)
  }
}
cat_indices <- split(seq_len(n_tasks),task_meta$category_short)
boot <- matrix(NA_real_,nrow=B,ncol=length(mods),dimnames=list(NULL,mods))
for (r in seq_len(B)) {
  idx <- unlist(lapply(cat_indices,function(ix) sample(ix,length(ix),replace=TRUE)),
                use.names=FALSE)
  for (j in seq_along(mods)) {
    boot[r,j] <- mean(arr[idx,j,"C1"]-arr[idx,j,"C0"])
  }
}
boot_summary <- data.frame()
for (m in mods) {
  point <- paired_df$risk_difference[paired_df$model==m]
  qs <- quantile(boot[,m],probs=c(.025,.975),names=FALSE,type=6)
  boot_summary <- rbind(boot_summary,data.frame(
    model=m,bootstrap_replicates=B,stratified_by_category=TRUE,
    point_risk_difference=point,point_difference_pp=100*point,
    percentile_ci_low=qs[1],percentile_ci_high=qs[2],
    percentile_ci_low_pp=100*qs[1],percentile_ci_high_pp=100*qs[2],
    stringsAsFactors=FALSE
  ))
}
write.csv(boot_summary,file.path(out_dir,"04_sensitivity_bootstrap_c1_minus_c0_v01.csv"),
          row.names=FALSE,fileEncoding="UTF-8")

# 5) A-F sensitivity descriptive table
cat_out <- data.frame()
for (m in mods) for (cnd in conds) for (ca in LETTERS[1:6]) {
  z <- subset(dat,model_short==m & condition_short==cnd & category_short==ca)
  ci <- wilson_ci(sum(z$sens_b),nrow(z))
  cat_out <- rbind(cat_out,data.frame(
    model=m,condition=cnd,category=ca,success=sum(z$sens_b),n=nrow(z),
    percent=100*mean(z$sens_b),
    wilson_low_percent=100*ci[1],wilson_high_percent=100*ci[2],
    stringsAsFactors=FALSE
  ))
}
write.csv(cat_out,file.path(out_dir,"05_sensitivity_category_v01.csv"),
          row.names=FALSE,fileEncoding="UTF-8")

# 6) Unblinded flips by model/condition/category
flip_cells <- aggregate(as.integer(dat$flip_b),
                        by=list(model=dat$model_short,condition=dat$condition_short,
                                category=dat$category_short),FUN=sum)
names(flip_cells)[4] <- "strict_to_sensitivity_flips"
flip_cells <- subset(flip_cells,strict_to_sensitivity_flips>0)
write.csv(flip_cells,file.path(out_dir,"06_flip_distribution_v01.csv"),
          row.names=FALSE,fileEncoding="UTF-8")

# 7) GLMM, same design and diagnostics
gdat <- dat
gdat$sens_num <- as.integer(gdat$sens_b)
gdat$model_f <- factor(gdat$model_short,levels=mods)
gdat$condition_f <- factor(gdat$condition_short,levels=conds)
gdat$category_f <- factor(gdat$category_short,levels=LETTERS[1:6])
gdat$task_f <- factor(gdat$task_id)

full_formula <- sens_num ~ model_f * condition_f + category_f + (1 | task_f)
reduced_formula <- sens_num ~ model_f + condition_f + category_f + (1 | task_f)

ctrl <- lme4::glmerControl(optimizer="bobyqa",
                           optCtrl=list(maxfun=200000),
                           check.conv.singular="ignore")
glmm <- lme4::glmer(full_formula,data=gdat,family=binomial(link="logit"),control=ctrl)
glmm_reduced <- lme4::glmer(reduced_formula,data=gdat,family=binomial(link="logit"),control=ctrl)

conv_msgs <- glmm@optinfo$conv$lme4$messages
converged <- is.null(conv_msgs)
singular <- lme4::isSingular(glmm,tol=1e-4)
co <- summary(glmm)$coefficients
separation_suspected <- any(!is.finite(co)) ||
  any(abs(co[,1])>20,na.rm=TRUE) ||
  any(co[,2]>10,na.rm=TRUE)
glmm_ok <- converged && !singular && !separation_suspected

diag <- data.frame(
  model="GLMM",converged=converged,singular=singular,
  separation_suspected=separation_suspected,
  accepted_as_primary=glmm_ok,
  convergence_message=ifelse(is.null(conv_msgs),"",paste(conv_msgs,collapse=" | ")),
  stringsAsFactors=FALSE
)
write.csv(diag,file.path(out_dir,"07_glmm_diagnostics_v01.csv"),
          row.names=FALSE,fileEncoding="UTF-8")
capture.output(summary(glmm),file=file.path(out_dir,"07b_glmm_summary_v01.txt"))
saveRDS(glmm,file.path(out_dir,"07c_glmm_model_v01.rds"))

fixed <- data.frame(
  term=rownames(co),estimate_log_odds=co[,1],std_error=co[,2],
  z_value=co[,3],p_value=co[,4],odds_ratio=exp(co[,1]),
  ci_low_or=exp(co[,1]-1.96*co[,2]),ci_high_or=exp(co[,1]+1.96*co[,2]),
  row.names=NULL,stringsAsFactors=FALSE
)
write.csv(fixed,file.path(out_dir,"08_glmm_fixed_effects_or_v01.csv"),
          row.names=FALSE,fileEncoding="UTF-8")

lrt <- anova(glmm_reduced,glmm,test="Chisq")
lrt_df <- data.frame(
  comparison="model_by_condition_interaction",
  df_difference=as.numeric(attr(logLik(glmm),"df")-attr(logLik(glmm_reduced),"df")),
  chisq=lrt$Chisq[2],p_value=lrt$`Pr(>Chisq)`[2],
  stringsAsFactors=FALSE
)
write.csv(lrt_df,file.path(out_dir,"09_glmm_interaction_lrt_v01.csv"),
          row.names=FALSE,fileEncoding="UTF-8")

emm <- emmeans::emmeans(glmm,~ condition_f | model_f)
emm_prob <- as.data.frame(summary(emm,type="response",infer=c(TRUE,TRUE)))
write.csv(emm_prob,file.path(out_dir,"10_glmm_emmeans_probability_v01.csv"),
          row.names=FALSE,fileEncoding="UTF-8")

ctr <- emmeans::contrast(emm,method=list(C1_vs_C0=c(-1,1)),
                         by="model_f",adjust="none")
ctr_df <- as.data.frame(summary(ctr,type="response",infer=c(TRUE,TRUE),adjust="none"))
ctr_df$holm_p <- p.adjust(ctr_df$p.value,method="holm")
write.csv(ctr_df,file.path(out_dir,"11_glmm_model_specific_c1_vs_c0_v01.csv"),
          row.names=FALSE,fileEncoding="UTF-8")

# 8) Pre-specified GEE fallback only if GLMM unacceptable
fallback_used <- FALSE
if (!glmm_ok) {
  fallback_used <- TRUE
  if (!requireNamespace("geepack",quietly=TRUE)) {
    stop("GLMM triggered fallback but geepack is not installed.")
  }
  gee <- geepack::geeglm(
    sens_num ~ model_f * condition_f + category_f,
    id=task_f,data=gdat,family=binomial(link="logit"),
    corstr="exchangeable",std.err="san.se"
  )
  capture.output(summary(gee),file=file.path(out_dir,"12_gee_fallback_summary_v01.txt"))
  saveRDS(gee,file.path(out_dir,"12b_gee_fallback_model_v01.rds"))
  gc <- summary(gee)$coefficients
  gee_fixed <- data.frame(
    term=rownames(gc),estimate_log_odds=gc[,1],std_error=gc[,2],
    wald=gc[,3],p_value=gc[,4],odds_ratio=exp(gc[,1]),
    ci_low_or=exp(gc[,1]-1.96*gc[,2]),ci_high_or=exp(gc[,1]+1.96*gc[,2]),
    row.names=NULL,stringsAsFactors=FALSE
  )
  write.csv(gee_fixed,file.path(out_dir,"13_gee_fixed_effects_or_v01.csv"),
            row.names=FALSE,fileEncoding="UTF-8")

  beta <- coef(gee); V <- vcov(gee)
  int_idx <- grep("model_f.*:condition_f|condition_f.*:model_f",names(beta))
  if (length(int_idx)==2) {
    bb <- beta[int_idx]; VV <- V[int_idx,int_idx,drop=FALSE]
    W <- as.numeric(t(bb)%*%solve(VV,bb))
    gee_int <- data.frame(
      comparison="model_by_condition_interaction",df=2,
      wald_chisq=W,p_value=pchisq(W,df=2,lower.tail=FALSE)
    )
    write.csv(gee_int,file.path(out_dir,"14_gee_interaction_wald_v01.csv"),
              row.names=FALSE,fileEncoding="UTF-8")
  }
}

# 9) Compact summary
summary_lines <- c(
  "# Sensitivity statistics V0.1 — compact summary",
  "",
  paste0("- Input SHA256: `",sha256_file(input_path),"`"),
  "- Same 540 Initial observations; Primary strict scoring unchanged",
  "- Strict FAIL→sensitivity PASS flips: 55",
  paste0("- Bootstrap: ",B," A–F-stratified task-level replicates"),
  paste0("- Sensitivity GLMM accepted: **",glmm_ok,"**"),
  paste0("- GEE fallback used: **",fallback_used,"**"),
  "",
  "## Strict vs sensitivity success"
)
for (i in seq_len(nrow(cmp))) {
  z <- cmp[i,]
  summary_lines <- c(summary_lines,
    sprintf("- %s %s: strict %d/90 = %.1f%% -> sensitivity %d/90 = %.1f%% (%+.1f pp; flips=%d)",
            z$model,z$condition,z$strict_success,z$strict_percent,
            z$sensitivity_success,z$sensitivity_percent,z$gain_pp,z$flips))
}
summary_lines <- c(summary_lines,"","## Sensitivity paired C1 − C0")
for (i in seq_len(nrow(paired_df))) {
  z <- paired_df[i,]
  bs <- boot_summary[boot_summary$model==z$model,]
  summary_lines <- c(summary_lines,
    sprintf("- %s: %+.1f pp; FAIL→PASS=%d, PASS→FAIL=%d; bootstrap 95%% CI [%+.1f, %+.1f] pp; McNemar p=%g; Holm p=%g",
            z$model,z$risk_difference_pp,z$fail_to_pass,z$pass_to_fail,
            bs$percentile_ci_low_pp,bs$percentile_ci_high_pp,
            z$exact_mcnemar_p,z$holm_p))
}
summary_lines <- c(summary_lines,"","## Model × condition interaction",
  sprintf("- sensitivity GLMM LRT: chi-square=%.4f, df=%d, p=%g",
          lrt_df$chisq,lrt_df$df_difference,lrt_df$p_value))
writeLines(summary_lines,file.path(out_dir,"00_sensitivity_statistics_summary_v01.md"))

pkg_versions <- c(
  R=R.version.string,
  lme4=as.character(utils::packageVersion("lme4")),
  emmeans=as.character(utils::packageVersion("emmeans")),
  geepack=if (requireNamespace("geepack",quietly=TRUE))
    as.character(utils::packageVersion("geepack")) else "not installed"
)
manifest_lines <- c(
  paste("generated_utc",format(Sys.time(),tz="UTC",usetz=TRUE),sep="\t"),
  paste("input_csv",input_path,sep="\t"),
  paste("input_sha256",sha256_file(input_path),sep="\t"),
  paste("strict_to_sensitivity_flips",sum(dat$flip_b),sep="\t"),
  paste("bootstrap_seed",20261001,sep="\t"),
  paste("bootstrap_replicates",B,sep="\t"),
  paste("glmm_accepted",glmm_ok,sep="\t"),
  paste("gee_fallback_used",fallback_used,sep="\t"),
  paste(names(pkg_versions),pkg_versions,sep="\t")
)
writeLines(manifest_lines,file.path(out_dir,"99_sensitivity_analysis_manifest_v01.tsv"))

cat("Sensitivity statistics V0.1 complete.\n")
cat("GLMM accepted:",glmm_ok,"\n")
cat("GEE fallback:",fallback_used,"\n")

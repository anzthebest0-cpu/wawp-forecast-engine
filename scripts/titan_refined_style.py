"""Shared cartography for fixed-cycle experimental spatial maps."""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.patches import Patch
from matplotlib.colors import BoundaryNorm
from mpl_toolkits.basemap import Basemap
from datetime import timedelta
DOMAINS={"regional":(110,132,-10,5),"sulawesi":(119,128,-8,0),"kolaka":(120.5,123.5,-6,-3)}
def figure(n,title,domain,lead,init,source,unit,levels,cmap,extend="both"):
    cols=2 if n>1 else 1;rows=(n+cols-1)//cols
    fig,axs=plt.subplots(rows,cols,figsize=(13.5 if cols==2 else 9.5,5.4*rows+1.5),squeeze=False)
    fig.subplots_adjust(left=.08,right=.96,bottom=.17/rows,top=.86 if rows==1 else .91,hspace=.28,wspace=.20)
    valid=init+timedelta(hours=lead)
    fig.suptitle(title+"\n"+domain.capitalize()+f" | Init {init:%d %b %Y %HZ} | H+{lead} | Valid {valid:%d %b %Y %HZ}",fontsize=16,weight="semibold",y=.98)
    fig.text(.5,.024,source+" | Archived research guidance; not an official forecast",ha="center",fontsize=9,color="#46596a")
    fig.legend(handles=[Patch(facecolor="#d9dbe0",edgecolor="#777",label="Masked / unavailable (not zero)")],loc="lower left",bbox_to_anchor=(.075,.04),frameon=False,fontsize=9)
    axes=list(axs.flat)
    for ax in axes[n:]:ax.set_visible(False)
    norm=BoundaryNorm(levels,plt.get_cmap(cmap).N,extend=extend)
    scalar=plt.cm.ScalarMappable(norm=norm,cmap=cmap)
    cb=fig.colorbar(scalar,ax=axes[:n],orientation="horizontal",fraction=.035,pad=.09,aspect=45,extend=extend,ticks=levels if len(levels)<=13 else levels[::2])
    cb.set_label(unit,fontsize=11);cb.ax.tick_params(labelsize=10)
    return fig,axes[:n],norm
def base(ax,domain):
    b=DOMAINS[domain]
    m=Basemap(projection="cyl",llcrnrlon=b[0],urcrnrlon=b[1],llcrnrlat=b[2],urcrnrlat=b[3],resolution="i",ax=ax)
    ax.set_facecolor("#d9dbe0")
    return m
def finish(ax,m,domain):
    b=DOMAINS[domain];step={"regional":5,"sulawesi":2,"kolaka":1}[domain]
    coast=m.drawcoastlines(linewidth=.8,color="#182531");coast.set_path_effects([pe.Stroke(linewidth=2.0,foreground="white",alpha=.8),pe.Normal()]);m.drawcountries(linewidth=.35,color="#526372")
    m.drawparallels(np.arange(-10,6,step),labels=[1,0,0,0],fontsize=8,linewidth=.3,color="#788690")
    m.drawmeridians(np.arange(110,133,step),labels=[0,0,0,1],fontsize=8,linewidth=.3,color="#788690")
    ax.plot(121.525,-4.338,marker="*",color="#111827",markeredgecolor="white",markeredgewidth=.5,ms=9,zorder=9)
    ax.annotate("WAWP",(121.525,-4.338),xytext=(5,5),textcoords="offset points",fontsize=8,zorder=10,bbox=dict(facecolor="white",alpha=.8,edgecolor="none",pad=1))
    if domain=="kolaka":
        # Approximate locality references, not station/grid coordinates.
        for name,x,y,offset in [("Kolaka",121.60556,-4.0597,(6,12)),("Pomalaa / Dawi-Dawi",121.6152,-4.1749,(8,-7)),("Mekongga",121.2978,-3.6907,(6,5))]:
            ax.plot(x,y,marker="o",ms=3.5,color="#111827",markeredgecolor="white",markeredgewidth=.6,zorder=9)
            ax.annotate(name,(x,y),xytext=offset,textcoords="offset points",fontsize=8,zorder=10,path_effects=[pe.withStroke(linewidth=2.4,foreground="white")])
        ax.text(120.85,-4.8,"Teluk Bone",fontsize=9,style="italic",color="#182531",zorder=9,path_effects=[pe.withStroke(linewidth=2.4,foreground="white")])
    ax.set_xlim(b[:2]);ax.set_ylim(b[2:]);ax.set_aspect("equal",adjustable="box")
def raster(ax,x,y,a,norm,cmap):
    palette=plt.get_cmap(cmap).copy();palette.set_bad("#d9dbe0")
    return ax.pcolormesh(x,y,np.ma.masked_invalid(a),shading="nearest",cmap=palette,norm=norm,rasterized=True)
def save(fig,path):
    fig.savefig(path,dpi=135,facecolor="white");plt.close(fig)
